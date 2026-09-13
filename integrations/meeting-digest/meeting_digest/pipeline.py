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
from datetime import date, datetime, timedelta, timezone
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
    skipped_short: int = 0
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
            "skipped_short": self.skipped_short,
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
        pending = _select_pending(candidates, sink_names, state, summary, config.min_duration_minutes)
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
    collected = _list_between(config, client, start_date, end_date)
    summary.listed = len(collected)
    logger.info("listed %d conversations since %s", summary.listed, start_date.isoformat())
    return collected


def _select_pending(
    candidates: List[Dict[str, Any]],
    sink_names: Sequence[str],
    state: DeliveryState,
    summary: RunSummary,
    min_duration_minutes: int = 0,
) -> List[Dict[str, Any]]:
    pending = []
    for item in candidates:
        conversation_id = item.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            continue
        if not state.pending_sinks(conversation_id, sink_names):
            summary.already_delivered += 1
            continue
        # Filtered before the transcript fetch, so a skipped conversation costs
        # nothing against the 25/hour transcript budget.
        if min_duration_minutes and _duration_minutes(item) < min_duration_minutes:
            summary.skipped_short += 1
            continue
        pending.append(item)

    pending.sort(key=_sort_key)
    return pending


def _duration_minutes(item: Dict[str, Any]) -> float:
    """Length from the list payload. An unknown length counts as long enough:
    dropping a conversation we cannot measure would lose it silently."""
    started = parse_timestamp(item.get("started_at"))
    finished = parse_timestamp(item.get("finished_at"))
    if not started or not finished or finished <= started:
        return float("inf")
    return (finished - started).total_seconds() / 60.0


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


@dataclass
class DailyRunSummary:
    day: str
    listed: int = 0
    in_day: int = 0
    delivered_to: List[str] = field(default_factory=list)
    skipped_sinks: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "listed": self.listed,
            "in_day": self.in_day,
            "delivered_to": list(self.delivered_to),
            "skipped_sinks": list(self.skipped_sinks),
            "failures": list(self.failures),
        }


def run_daily(
    config: Config,
    client: OmiClient,
    sinks: Sequence[Sink],
    day: date,
) -> DailyRunSummary:
    """Build and deliver one day's review.

    Costs only list reads: the roll-up needs titles, overviews, action items and
    events, all of which the list endpoint returns without transcripts. The
    25/hour transcript budget is untouched, so this is safe to re-run.
    """
    from .daily import build_daily_summary, local_day_bounds

    summary = DailyRunSummary(day=day.isoformat())
    start_utc, end_utc = local_day_bounds(day, config.utc_offset_hours)

    raw = _list_between(config, client, start_utc, end_utc)
    summary.listed = len(raw)

    records = [MeetingRecord.from_api(item) for item in raw]
    daily = build_daily_summary(records, day, config.utc_offset_hours)
    summary.in_day = daily.conversation_count

    for sink in sinks:
        if not sink.supports_daily:
            summary.skipped_sinks.append(sink.name)
            continue
        try:
            sink.deliver_daily(daily)
        except SinkError as exc:
            summary.failures.append(str(exc))
            logger.error("%s", exc)
        except Exception as exc:
            summary.failures.append("{}: unexpected {} on the {} review".format(sink.name, type(exc).__name__, day))
            logger.exception("sink %s raised on the %s review", sink.name, day)
        else:
            summary.delivered_to.append(sink.name)
            logger.info("delivered the %s review to %s", day.isoformat(), sink.name)

    return summary


def _list_between(config: Config, client: OmiClient, start_utc: datetime, end_utc: datetime) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    seen_ids = set()

    for page in range(MAX_LIST_PAGES):
        batch = client.list_conversations(
            limit=config.list_page_size,
            offset=page * config.list_page_size,
            start_date=start_utc,
            end_date=end_utc,
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

    return collected
