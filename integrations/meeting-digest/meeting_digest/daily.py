"""The day's review: one roll-up of everything captured on a calendar day.

Built entirely from data Omi already produced — per-conversation overviews,
action items, events — with no extra LLM call. That keeps the roll-up free, and
it keeps it honest: nothing here is a second-hand summary of a summary.

"A day" is a local calendar day, not a UTC one. With the default +9 offset a
conversation at 08:00 JST belongs to that morning, not to the previous UTC day.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .models import ActionItem, Event, MeetingRecord


@dataclass(frozen=True)
class DailySummary:
    day: date
    utc_offset_hours: int
    conversations: List[MeetingRecord] = field(default_factory=list)

    @property
    def conversation_count(self) -> int:
        return len(self.conversations)

    @property
    def total_minutes(self) -> int:
        return sum(record.duration_minutes or 0 for record in self.conversations)

    @property
    def category_counts(self) -> List[Tuple[str, int]]:
        counts: Dict[str, int] = {}
        for record in self.conversations:
            key = record.category or "other"
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    @property
    def open_actions(self) -> List[Tuple[MeetingRecord, ActionItem]]:
        pairs = []
        for record in self.conversations:
            for item in record.open_action_items:
                pairs.append((record, item))
        # Dated commitments first, soonest first; undated ones keep capture order.
        return sorted(pairs, key=lambda pair: (pair[1].due_at is None, pair[1].due_at or _FAR_FUTURE))

    @property
    def upcoming_events(self) -> List[Tuple[MeetingRecord, Event]]:
        pairs = [(record, event) for record in self.conversations for event in record.events]
        return sorted(pairs, key=lambda pair: (pair[1].start is None, pair[1].start or _FAR_FUTURE))

    @property
    def is_empty(self) -> bool:
        return not self.conversations

    @property
    def label(self) -> str:
        return self.day.isoformat()


_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def local_day_bounds(day: date, utc_offset_hours: int) -> Tuple[datetime, datetime]:
    """The UTC instants bounding a local calendar day, end-exclusive."""
    tz = timezone(timedelta(hours=utc_offset_hours))
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start_local.astimezone(timezone.utc), (start_local + timedelta(days=1)).astimezone(timezone.utc)


def resolve_day(value: Optional[str], utc_offset_hours: int, now: Optional[datetime] = None) -> date:
    """Turn --day into a calendar date: an ISO date, 'today', or 'yesterday'."""
    tz = timezone(timedelta(hours=utc_offset_hours))
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    text = (value or "yesterday").strip().lower()

    if text == "today":
        return local_now.date()
    if text == "yesterday":
        return (local_now - timedelta(days=1)).date()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("--day must be an ISO date (YYYY-MM-DD), 'today', or 'yesterday'; got {!r}".format(value))


def build_daily_summary(
    records: Sequence[MeetingRecord],
    day: date,
    utc_offset_hours: int,
) -> DailySummary:
    """Keep the conversations whose local start date is `day`, oldest first."""
    start_utc, end_utc = local_day_bounds(day, utc_offset_hours)

    in_day = []
    for record in records:
        occurred = record.occurred_at
        if occurred and start_utc <= occurred < end_utc:
            in_day.append(record)

    in_day.sort(key=lambda record: record.occurred_at or start_utc)
    return DailySummary(day=day, utc_offset_hours=utc_offset_hours, conversations=in_day)
