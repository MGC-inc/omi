"""Fetch new conversations and deliver them to the configured sinks.

Shape of one run:

1. List conversations in the lookback window **without transcripts**. The list
   endpoint is the cheap budget (60/hour); transcripts are the scarce one
   (25/hour), so nothing pays for a transcript before we know it is needed.
2. Drop conversations already delivered to every configured sink.
3. For the rest, oldest first, fetch the full conversation with its transcript,
   up to ``max_transcript_fetches`` per run. Whatever does not fit is picked up
   by the next run — the work is a queue, not a deadline.
4. Deliver to each sink that has not yet received it, recording each success
   individually so a failing sink never re-sends the ones that worked.

Partial progress is always preserved: state is saved even when the run aborts on
a rate limit or an unexpected error.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from .client import OmiApiError, OmiClient, RateLimitedError
from .config import Config
from .models import MeetingRecord, parse_timestamp
from .sinks.base import Sink, SinkError
from .state import DeliveryState

logger = logging.getLogger(__name__)

MAX_LIST_PAGES = 10


@dataclass
class RunSummary:
    listed: int = 0
    already_delivered: int = 0
    fetched: int = 0
    delivered: int = 0
    deferred: int = 0
    failures: List[str] = field(default_factory=list)
    rate_limited: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures and not self.rate_limited

    def as_dict(self) -> Dict[str, Any]:
        return {
            "listed": self.listed,
            "already_delivered": self.already_delivered,
            "fetched": self.fetched,
            "delivered": self.delivered,
            "deferred": self.deferred,
            "failures": list(self.failures),
            "rate_limited": self.rate_limited,
        }


def run(
    config: Config,
    client: OmiClient,
    sinks: Sequence[Sink],
    state: DeliveryState,
    now: Optional[datetime] = None,
) -> RunSummary:
    summary = RunSummary()
    sink_names = [sink.name for sink in sinks]
    if not sink_names:
        raise ValueError("At least one sink is required")

    current_time = now or datetime.now(timezone.utc)
    start_date = current_time - timedelta(hours=config.lookback_hours)

    try:
        candidates = _list_window(config, client, start_date, current_time, summary)
        pending = _select_pending(candidates, sink_names, state, summary)
        _deliver_all(config, client, sinks, state, pending, summary)
    finally:
        state.save()

    return summary


def _list_window(
    config: Config,
    client: OmiClient,
    start_date: datetime,
    end_date: datetime,
    summary: RunSummary,
) -> List[Dict[str, Any]]:
    """Page through the lookback window, transcripts excluded."""
    collected: List[Dict[str, Any]] = []
    seen_ids = set()

    for page in range(MAX_LIST_PAGES):
        batch = client.list_conversations(
            limit=config.list_page_size,
            offset=page * config.list_page_size,
            start_date=start_date,
            end_date=end_date,
            categories=config.categories,
            include_transcript=False,
        )
        if not batch:
            break
        for item in batch:
            conversation_id = item.get("id")
            if isinstance(conversation_id, str) and conversation_id and conversation_id not in seen_ids:
                seen_ids.add(conversation_id)
                collected.append(item)
        if len(batch) < config.list_page_size:
            break

    summary.listed = len(collected)
    logger.info("listed %d conversations since %s", summary.listed, start_date.isoformat())
    return collected


def _select_pending(
    candidates: List[Dict[str, Any]],
    sink_names: Sequence[str],
    state: DeliveryState,
    summary: RunSummary,
) -> List[Dict[str, Any]]:
    pending = []
    for item in candidates:
        conversation_id = item.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            continue
        if state.pending_sinks(conversation_id, sink_names):
            pending.append(item)
        else:
            summary.already_delivered += 1

    pending.sort(key=_sort_key)
    return pending


def _sort_key(item: Dict[str, Any]) -> datetime:
    """Oldest first, so an interrupted backlog drains in chronological order."""
    for key in ("started_at", "created_at", "finished_at"):
        parsed = parse_timestamp(item.get(key))
        if parsed:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _deliver_all(
    config: Config,
    client: OmiClient,
    sinks: Sequence[Sink],
    state: DeliveryState,
    pending: List[Dict[str, Any]],
    summary: RunSummary,
) -> None:
    budget = config.max_transcript_fetches

    for index, item in enumerate(pending):
        conversation_id = item["id"]

        if index >= budget:
            summary.deferred = len(pending) - budget
            logger.info(
                "transcript budget (%d) reached; deferring %d conversation(s) to the next run",
                budget,
                summary.deferred,
            )
            return

        try:
            payload = client.get_conversation(conversation_id, include_transcript=True)
        except RateLimitedError as exc:
            summary.rate_limited = True
            summary.deferred = len(pending) - index
            logger.warning("rate limited after %d fetch(es): %s", summary.fetched, exc)
            return
        except OmiApiError as exc:
            summary.failures.append("fetch {}: {}".format(conversation_id, exc))
            logger.error("could not fetch %s: %s", conversation_id, exc)
            continue

        summary.fetched += 1
        record = MeetingRecord.from_api(payload)
        if not record.id:
            summary.failures.append("fetch {}: response carried no id".format(conversation_id))
            continue

        _deliver_one(sinks, state, record, summary)


def _deliver_one(sinks: Sequence[Sink], state: DeliveryState, record: MeetingRecord, summary: RunSummary) -> None:
    for sink in sinks:
        if state.is_delivered(record.id, sink.name):
            continue
        try:
            sink.deliver(record)
        except SinkError as exc:
            summary.failures.append(str(exc))
            logger.error("%s", exc)
            continue
        except Exception as exc:  # a sink must never take the whole run down
            summary.failures.append("{}: unexpected {} for {}".format(sink.name, type(exc).__name__, record.id))
            logger.exception("sink %s raised on %s", sink.name, record.id)
            continue

        state.mark_delivered(record.id, sink.name)
        summary.delivered += 1
        logger.info("delivered %s to %s", record.id, sink.name)
