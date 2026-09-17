"""Normalized internal shapes for Omi conversations.

The Developer API returns the full ``Conversation`` model, which carries far
more than an internal digest needs and evolves upstream. Everything downstream
of this module reads these dataclasses instead, so an added or renamed upstream
field changes one file rather than every sink.

Parsing is deliberately tolerant: unknown fields are dropped, missing fields
fall back to empty values, and a malformed timestamp yields ``None`` rather than
failing the whole run. A single odd conversation must not stop a batch.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an API timestamp into an aware UTC datetime, or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # Python 3.9's fromisoformat rejects the trailing 'Z' the API emits.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ActionItem:
    description: str
    completed: bool = False
    due_at: Optional[datetime] = None
    owner_name: Optional[str] = None
    context: Optional[str] = None

    @staticmethod
    def from_api(payload: Dict[str, Any]) -> "ActionItem":
        return ActionItem(
            description=_text(payload.get("description")),
            completed=bool(payload.get("completed", False)),
            due_at=parse_timestamp(payload.get("due_at")),
            owner_name=_optional_text(payload.get("owner_name")),
            context=_optional_text(payload.get("context")),
        )


@dataclass(frozen=True)
class Section:
    heading: str
    body_markdown: str

    @staticmethod
    def from_api(payload: Dict[str, Any]) -> "Section":
        return Section(
            heading=_text(payload.get("heading")),
            body_markdown=_text(payload.get("body_markdown")),
        )


@dataclass(frozen=True)
class Event:
    title: str
    description: str
    start: Optional[datetime]
    duration_minutes: int

    @staticmethod
    def from_api(payload: Dict[str, Any]) -> "Event":
        duration = payload.get("duration")
        return Event(
            title=_text(payload.get("title")),
            description=_text(payload.get("description")),
            start=parse_timestamp(payload.get("start")),
            duration_minutes=duration if isinstance(duration, int) and duration > 0 else 30,
        )


@dataclass(frozen=True)
class Utterance:
    speaker: str
    text: str
    is_user: bool
    start_seconds: float
    end_seconds: float

    @staticmethod
    def from_api(payload: Dict[str, Any]) -> "Utterance":
        return Utterance(
            speaker=_text(payload.get("speaker")) or "SPEAKER_00",
            text=_text(payload.get("text")),
            is_user=bool(payload.get("is_user", False)),
            start_seconds=_number(payload.get("start")),
            end_seconds=_number(payload.get("end")),
        )


@dataclass(frozen=True)
class MeetingRecord:
    """One conversation, reduced to what internal delivery needs."""

    id: str
    title: str
    overview: str
    category: str
    emoji: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: Optional[datetime]
    language: Optional[str]
    source: Optional[str]
    sections: List[Section] = field(default_factory=list)
    action_items: List[ActionItem] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    transcript: List[Utterance] = field(default_factory=list)
    #: A cleaned reading copy of the transcript (see refine.py). Always an
    #: addition — `transcript` stays as the device heard it.
    refined_transcript: Optional[str] = None
    #: What curation pulled out of the conversation (see curate.py).
    ideas: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    curation_reason: Optional[str] = None
    #: True when a spoken trigger phrase marked this conversation.
    clipped: bool = False

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript)

    @property
    def occurred_at(self) -> Optional[datetime]:
        """Best available "when did this happen", for filenames and ordering."""
        return self.started_at or self.created_at or self.finished_at

    @property
    def duration_minutes(self) -> Optional[int]:
        if not self.started_at or not self.finished_at:
            return None
        seconds = (self.finished_at - self.started_at).total_seconds()
        if seconds <= 0:
            return None
        return int(round(seconds / 60.0))

    @property
    def open_action_items(self) -> List[ActionItem]:
        return [item for item in self.action_items if not item.completed]

    def to_dict(self, include_transcript: bool = True) -> Dict[str, Any]:
        """A JSON-serializable projection — the shape the HTTP API returns."""
        payload: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "overview": self.overview,
            "category": self.category,
            "emoji": self.emoji,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "created_at": _iso(self.created_at),
            "duration_minutes": self.duration_minutes,
            "language": self.language,
            "source": self.source,
            "sections": [{"heading": s.heading, "body_markdown": s.body_markdown} for s in self.sections],
            "action_items": [
                {
                    "description": item.description,
                    "completed": item.completed,
                    "due_at": _iso(item.due_at),
                    "owner_name": item.owner_name,
                    "context": item.context,
                }
                for item in self.action_items
            ],
            "events": [
                {
                    "title": event.title,
                    "description": event.description,
                    "start": _iso(event.start),
                    "duration_minutes": event.duration_minutes,
                }
                for event in self.events
            ],
        }
        if self.refined_transcript:
            payload["refined_transcript"] = self.refined_transcript
        if self.ideas:
            payload["ideas"] = list(self.ideas)
        if self.topics:
            payload["topics"] = list(self.topics)
        if self.curation_reason:
            payload["curation_reason"] = self.curation_reason
        if self.clipped:
            payload["clipped"] = True
        if include_transcript:
            payload["transcript"] = [
                {
                    "speaker": u.speaker,
                    "text": u.text,
                    "is_user": u.is_user,
                    "start_seconds": u.start_seconds,
                    "end_seconds": u.end_seconds,
                }
                for u in self.transcript
            ]
        return payload

    def with_refined_transcript(self, cleaned: str) -> "MeetingRecord":
        return replace(self, refined_transcript=cleaned)

    def with_curation(self, curation) -> "MeetingRecord":
        return replace(
            self,
            ideas=list(curation.ideas),
            topics=list(curation.topics),
            curation_reason=curation.reason or None,
            clipped=curation.clipped,
        )

    @staticmethod
    def from_stored(payload: Dict[str, Any]) -> "MeetingRecord":
        """Rebuild a record from ``to_dict`` output.

        The inverse of ``to_dict``, so the stored copy feeds the same daily
        roll-up code as a freshly fetched one instead of a parallel aggregation.
        """
        return MeetingRecord(
            id=_text(payload.get("id")),
            title=_text(payload.get("title")) or "(untitled)",
            overview=_text(payload.get("overview")),
            category=_text(payload.get("category")) or "other",
            emoji=_text(payload.get("emoji")) or "🧠",
            started_at=parse_timestamp(payload.get("started_at")),
            finished_at=parse_timestamp(payload.get("finished_at")),
            created_at=parse_timestamp(payload.get("created_at")),
            language=_optional_text(payload.get("language")),
            source=_optional_text(payload.get("source")),
            sections=[Section.from_api(s) for s in _items(payload.get("sections"))],
            action_items=[ActionItem.from_api(a) for a in _items(payload.get("action_items"))],
            events=[
                Event(
                    title=_text(e.get("title")),
                    description=_text(e.get("description")),
                    start=parse_timestamp(e.get("start")),
                    duration_minutes=(
                        e["duration_minutes"]
                        if isinstance(e.get("duration_minutes"), int) and e["duration_minutes"] > 0
                        else 30
                    ),
                )
                for e in _items(payload.get("events"))
            ],
            transcript=[
                Utterance(
                    speaker=_text(t.get("speaker")) or "SPEAKER_00",
                    text=_text(t.get("text")),
                    is_user=bool(t.get("is_user", False)),
                    start_seconds=_number(t.get("start_seconds")),
                    end_seconds=_number(t.get("end_seconds")),
                )
                for t in _items(payload.get("transcript"))
            ],
            refined_transcript=_optional_text(payload.get("refined_transcript")),
            ideas=[str(x) for x in (payload.get("ideas") or []) if str(x).strip()],
            topics=[str(x) for x in (payload.get("topics") or []) if str(x).strip()],
            curation_reason=_optional_text(payload.get("curation_reason")),
            clipped=bool(payload.get("clipped", False)),
        )

    @staticmethod
    def from_api(payload: Dict[str, Any]) -> "MeetingRecord":
        structured = payload.get("structured") or {}
        return MeetingRecord(
            id=_text(payload.get("id")),
            title=_text(structured.get("title")) or "(untitled)",
            overview=_text(structured.get("overview")),
            category=_text(structured.get("category")) or "other",
            emoji=_text(structured.get("emoji")) or "🧠",
            started_at=parse_timestamp(payload.get("started_at")),
            finished_at=parse_timestamp(payload.get("finished_at")),
            created_at=parse_timestamp(payload.get("created_at")),
            language=_optional_text(payload.get("language")),
            source=_optional_text(payload.get("source")),
            sections=[Section.from_api(s) for s in _items(structured.get("sections"))],
            action_items=[ActionItem.from_api(a) for a in _items(structured.get("action_items"))],
            events=[Event.from_api(e) for e in _items(structured.get("events"))],
            transcript=[Utterance.from_api(t) for t in _items(payload.get("transcript_segments"))],
        )


def to_local(value: Optional[datetime], utc_offset_hours: int) -> Optional[datetime]:
    """Shift an aware UTC datetime into the configured local offset."""
    if value is None:
        return None
    return value.astimezone(timezone(timedelta(hours=utc_offset_hours)))


def format_local(
    value: Optional[datetime], utc_offset_hours: int, fmt: str = "%Y-%m-%d %H:%M", empty: str = "-"
) -> str:
    """Render a timestamp in local time. Timestamps shown to people are local;
    only machine-readable fields stay in UTC."""
    local = to_local(value, utc_offset_hours)
    return local.strftime(fmt) if local else empty


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> Optional[str]:
    text = _text(value)
    return text or None


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
