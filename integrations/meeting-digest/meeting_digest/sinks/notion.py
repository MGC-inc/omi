"""Create one Notion page per conversation, inside a database.

Notion's own limits shape this module:

* a page create accepts at most 100 child blocks, so the rest are appended in
  further batches of 100;
* one rich-text run holds at most 2000 characters, so long text is split;
* a property that does not exist on the database is a 400, so the database
  schema is read once and only the properties it actually has are written.

That last point is why a minimal database (a title column and nothing else)
works: every other property is optional. Add the columns from README.md to get
date, category, duration, action counts, and the Omi ID back-reference.

Idempotency: when the database carries an "Omi ID" property, delivery first
queries for a page already holding this conversation's ID and skips if one
exists. Without that property the local delivery state is the only guard, which
is enough for ordinary runs but can duplicate a page if a run dies between
creating the page and saving state.

Column names are configurable (``property_names``). The canonical keys below are
what this module asks for; a real database decides what they are called, and in
a Japanese workspace that is rarely the English default.
"""

from typing import Any, Dict, List, Optional

import httpx

from ..models import ActionItem, MeetingRecord, Utterance, format_local
from .base import Sink, SinkError

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

MAX_BLOCKS_PER_REQUEST = 100
MAX_RICH_TEXT_CHARS = 2000

# Canonical property keys. property_names maps each to the database's own column.
OMI_ID_PROPERTY = "Omi ID"
DATE_PROPERTY = "Date"
CATEGORY_PROPERTY = "Category"
DURATION_PROPERTY = "Duration (min)"
OPEN_ACTIONS_PROPERTY = "Open Actions"
LANGUAGE_PROPERTY = "Language"


class NotionSink(Sink):
    name = "notion"
    supports_daily = True

    def __init__(
        self,
        token: str,
        database_id: str,
        # Default off: Omi's raw transcript carries recognition noise, and the
        # summary is the part worth putting in a shared workspace.
        include_transcript: bool = False,
        timeout_seconds: float = 30.0,
        client: Optional[httpx.Client] = None,
        utc_offset_hours: int = 9,
        property_names: Optional[Dict[str, str]] = None,
    ):
        if not token:
            raise ValueError("NotionSink requires an integration token")
        if not database_id:
            raise ValueError("NotionSink requires a database id")
        self._database_id = database_id
        self._include_transcript = include_transcript
        self._utc_offset_hours = utc_offset_hours
        self._property_names = dict(property_names or {})
        self._schema: Optional[Dict[str, Any]] = None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._headers = {
            "Authorization": "Bearer {}".format(token),
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @property
    def _omi_id_column(self) -> str:
        return self._property_names.get(OMI_ID_PROPERTY, OMI_ID_PROPERTY)

    def deliver(self, record: MeetingRecord) -> None:
        schema = self._database_schema()

        if self._omi_id_column in schema and self._already_present(record.id):
            return

        blocks = build_blocks(
            record, include_transcript=self._include_transcript, utc_offset_hours=self._utc_offset_hours
        )
        page_id = self._create_page(record, schema, blocks[:MAX_BLOCKS_PER_REQUEST])

        remaining = blocks[MAX_BLOCKS_PER_REQUEST:]
        for index in range(0, len(remaining), MAX_BLOCKS_PER_REQUEST):
            self._append_blocks(page_id, remaining[index : index + MAX_BLOCKS_PER_REQUEST])

    def deliver_daily(self, summary) -> None:
        """Re-runnable: a same-day page is archived, then written fresh.

        The day's review legitimately changes during the day, so skipping an
        existing page would leave a stale morning snapshot in place. Archiving
        (rather than editing blocks in place) keeps it to two calls and leaves
        the superseded version recoverable from Notion's trash.
        """
        schema = self._database_schema()
        synthetic_id = daily_record_id(summary)

        if self._omi_id_column in schema:
            for page_id in self._find_pages(synthetic_id):
                self._archive_page(page_id)

        blocks = build_daily_blocks(summary)
        page_id = self._create_daily_page(summary, schema, synthetic_id, blocks[:MAX_BLOCKS_PER_REQUEST])

        remaining = blocks[MAX_BLOCKS_PER_REQUEST:]
        for index in range(0, len(remaining), MAX_BLOCKS_PER_REQUEST):
            self._append_blocks(page_id, remaining[index : index + MAX_BLOCKS_PER_REQUEST])

    def _create_daily_page(
        self,
        summary,
        schema: Dict[str, Any],
        synthetic_id: str,
        blocks: List[Dict[str, Any]],
    ) -> str:
        body: Dict[str, Any] = {
            "parent": {"database_id": self._database_id},
            "properties": build_daily_properties(summary, schema, synthetic_id, self._property_names),
            "children": blocks,
            "icon": {"type": "emoji", "emoji": "🗓️"},
        }
        payload = self._request("POST", "/pages", json=body)
        page_id = payload.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise SinkError("notion: daily page create for {} returned no id".format(summary.label))
        return page_id

    def _find_pages(self, conversation_id: str) -> List[str]:
        payload = self._request(
            "POST",
            "/databases/{}/query".format(self._database_id),
            json={
                "filter": {"property": self._omi_id_column, "rich_text": {"equals": conversation_id}},
                "page_size": 25,
            },
        )
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        return [item["id"] for item in results if isinstance(item, dict) and isinstance(item.get("id"), str)]

    def _archive_page(self, page_id: str) -> None:
        self._request("PATCH", "/pages/{}".format(page_id), json={"archived": True})

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # --- Notion calls -------------------------------------------------------

    def _database_schema(self) -> Dict[str, Any]:
        if self._schema is not None:
            return self._schema

        payload = self._request("GET", "/databases/{}".format(self._database_id))
        properties = payload.get("properties")
        if not isinstance(properties, dict):
            raise SinkError("notion: database {} returned no properties".format(self._database_id))
        if not _title_property_name(properties):
            raise SinkError("notion: database {} has no title property".format(self._database_id))

        self._schema = properties
        return properties

    def _already_present(self, conversation_id: str) -> bool:
        return bool(self._find_pages(conversation_id))

    def _create_page(self, record: MeetingRecord, schema: Dict[str, Any], blocks: List[Dict[str, Any]]) -> str:
        body: Dict[str, Any] = {
            "parent": {"database_id": self._database_id},
            "properties": build_properties(record, schema, self._property_names),
            "children": blocks,
        }
        if record.emoji:
            body["icon"] = {"type": "emoji", "emoji": record.emoji}

        payload = self._request("POST", "/pages", json=body)
        page_id = payload.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise SinkError("notion: page create for {} returned no id".format(record.id))
        return page_id

    def _append_blocks(self, page_id: str, blocks: List[Dict[str, Any]]) -> None:
        self._request("PATCH", "/blocks/{}/children".format(page_id), json={"children": blocks})

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            response = self._client.request(method, NOTION_API + path, headers=self._headers, json=json)
        except httpx.HTTPError as exc:
            raise SinkError("notion: {} {} failed: {}".format(method, path, type(exc).__name__))

        if response.status_code not in (200, 201):
            raise SinkError(
                "notion: {} {} returned HTTP {} — {}".format(
                    method, path, response.status_code, _error_message(response)
                )
            )

        try:
            payload = response.json()
        except ValueError:
            raise SinkError("notion: {} {} returned a non-JSON body".format(method, path))
        if not isinstance(payload, dict):
            raise SinkError("notion: {} {} returned {}".format(method, path, type(payload).__name__))
        return payload


# --- payload construction ---------------------------------------------------


def build_properties(
    record: MeetingRecord, schema: Dict[str, Any], property_names: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Write the title, plus whichever optional properties the database has.

    A database missing every optional column still receives a usable page.
    """
    properties: Dict[str, Any] = {}

    title_name = _title_property_name(schema)
    if title_name:
        properties[title_name] = {"title": [{"text": {"content": _clip(record.title)}}]}

    optional = {
        DATE_PROPERTY: lambda: _date_property(record),
        CATEGORY_PROPERTY: lambda: {"select": {"name": record.category}} if record.category else None,
        DURATION_PROPERTY: lambda: {"number": record.duration_minutes},
        OPEN_ACTIONS_PROPERTY: lambda: {"number": len(record.open_action_items)},
        LANGUAGE_PROPERTY: lambda: {"select": {"name": record.language}} if record.language else None,
        OMI_ID_PROPERTY: lambda: {"rich_text": [{"text": {"content": record.id}}]} if record.id else None,
    }
    _apply_optional(properties, schema, optional, property_names)
    return properties


def _apply_optional(
    properties: Dict[str, Any],
    schema: Dict[str, Any],
    optional: Dict[str, Any],
    property_names: Optional[Dict[str, str]],
) -> None:
    names = property_names or {}
    for canonical, builder in optional.items():
        column = names.get(canonical, canonical)
        declared = schema.get(column)
        if not isinstance(declared, dict):
            continue
        value = builder()
        if value is None:
            continue
        # Skip a column whose type is not the one we write into it.
        if declared.get("type") not in value:
            continue
        properties[column] = value


def build_blocks(
    record: MeetingRecord, include_transcript: bool = True, utc_offset_hours: int = 9
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []

    if record.ideas:
        # First, because this is what the conversation was kept for.
        blocks.append(_heading("拾ったアイデア・気づき"))
        blocks.extend(_bullet(idea) for idea in record.ideas)

    if record.overview:
        blocks.append(_heading("概要"))
        blocks.extend(_paragraphs(record.overview))

    for section in record.sections:
        if not section.heading and not section.body_markdown:
            continue
        blocks.append(_heading(section.heading or "詳細"))
        blocks.extend(_paragraphs(section.body_markdown))

    if record.action_items:
        blocks.append(_heading("アクションアイテム"))
        blocks.extend(_todo(item, utc_offset_hours) for item in record.action_items)

    if record.events:
        blocks.append(_heading("予定"))
        for event in record.events:
            blocks.append(_bullet(_event_line(event, utc_offset_hours)))

    if include_transcript:
        # Prefer the cleaned copy when one exists: the raw transcript's
        # recognition noise is exactly what makes it unfit for a shared page.
        if record.refined_transcript:
            blocks.append(_heading("文字起こし（整形済み）"))
            for paragraph in record.refined_transcript.split("\n\n"):
                blocks.extend(_paragraphs(paragraph))
        elif record.transcript:
            blocks.append(_heading("文字起こし"))
            for utterance in record.transcript:
                blocks.extend(_paragraphs(_transcript_line(utterance)))

    return blocks


def _transcript_line(utterance: Utterance) -> str:
    speaker = "自分" if utterance.is_user else utterance.speaker
    total = max(0, int(utterance.start_seconds))
    return "{} [{:02d}:{:02d}] {}".format(speaker, total // 60, total % 60, utterance.text)


def _date_property(record: MeetingRecord) -> Optional[Dict[str, Any]]:
    occurred = record.occurred_at
    if not occurred:
        return None
    value: Dict[str, Any] = {"start": occurred.isoformat()}
    if record.finished_at and record.started_at and record.finished_at > record.started_at:
        value["end"] = record.finished_at.isoformat()
    return {"date": value}


def _heading(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": _clip(text)}}]},
    }


def _bullet(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": _clip(text)}}]},
    }


def _event_line(event, utc_offset_hours: int, suffix: str = "") -> str:
    when = (
        "{} UTC{:+d}".format(format_local(event.start, utc_offset_hours), utc_offset_hours)
        if event.start
        else "日時未定"
    )
    return "{} — {}（{}分）{}".format(event.title, when, event.duration_minutes, suffix)


def _todo(item: ActionItem, utc_offset_hours: int = 9) -> Dict[str, Any]:
    text = item.description or "(内容なし)"
    if item.owner_name:
        text += "（担当: {}）".format(item.owner_name)
    if item.due_at:
        text += "（期限: {}）".format(format_local(item.due_at, utc_offset_hours, fmt="%Y-%m-%d"))
    if item.context:
        text += " — {}".format(item.context)
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [{"type": "text", "text": {"content": _clip(text)}}],
            "checked": item.completed,
        },
    }


def _paragraphs(text: str) -> List[Dict[str, Any]]:
    """One paragraph block per 2000-character chunk; Notion rejects longer runs."""
    blocks = []
    for chunk in _chunks(text, MAX_RICH_TEXT_CHARS):
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
            }
        )
    return blocks or [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}]


def _chunks(text: str, size: int) -> List[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    return [stripped[index : index + size] for index in range(0, len(stripped), size)]


def _clip(text: str) -> str:
    value = text or ""
    return value if len(value) <= MAX_RICH_TEXT_CHARS else value[: MAX_RICH_TEXT_CHARS - 1] + "…"


def _title_property_name(schema: Dict[str, Any]) -> Optional[str]:
    for name, declared in schema.items():
        if isinstance(declared, dict) and declared.get("type") == "title":
            return name
    return None


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str):
            return message[:300]
    return ""


DAILY_ID_PREFIX = "daily-"


def daily_record_id(summary) -> str:
    """The synthetic Omi ID that lets a day's review be found and replaced."""
    return DAILY_ID_PREFIX + summary.label


def build_daily_properties(
    summary, schema: Dict[str, Any], synthetic_id: str, property_names: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}

    title_name = _title_property_name(schema)
    if title_name:
        properties[title_name] = {"title": [{"text": {"content": "🗓️ {} の振り返り".format(summary.label)}}]}

    optional = {
        DATE_PROPERTY: lambda: {"date": {"start": summary.label}},
        CATEGORY_PROPERTY: lambda: {"select": {"name": "daily-review"}},
        DURATION_PROPERTY: lambda: {"number": summary.total_minutes},
        OPEN_ACTIONS_PROPERTY: lambda: {"number": len(summary.open_actions)},
        OMI_ID_PROPERTY: lambda: {"rich_text": [{"text": {"content": synthetic_id}}]},
    }
    _apply_optional(properties, schema, optional, property_names)
    return properties


def build_daily_blocks(summary) -> List[Dict[str, Any]]:
    if summary.is_empty:
        return _paragraphs("この日に記録された会話はありません。")

    offset = summary.utc_offset_hours
    blocks: List[Dict[str, Any]] = _paragraphs(
        "会話 {}件 ・ 合計 {}分（UTC{:+d} 基準）".format(
            summary.conversation_count, summary.total_minutes, summary.utc_offset_hours
        )
    )

    categories = summary.category_counts
    if categories:
        blocks.extend(
            _paragraphs("カテゴリ: " + " ・ ".join("{} {}件".format(name, count) for name, count in categories))
        )

    open_actions = summary.open_actions
    if open_actions:
        blocks.append(_heading("未完了のアクション"))
        for record, item in open_actions:
            blocks.append(_todo(item, offset))
            blocks.append(_bullet("↑ {}".format(record.title)))

    events = summary.upcoming_events
    if events:
        blocks.append(_heading("予定"))
        for record, event in events:
            blocks.append(_bullet(_event_line(event, offset, " ← {}".format(record.title))))

    blocks.append(_heading("この日の会話"))
    for record in summary.conversations:
        stamp = format_local(record.occurred_at, offset, fmt="%H:%M", empty="--:--")
        duration = "{}分".format(record.duration_minutes) if record.duration_minutes else "-"
        blocks.append(_bullet("{} {} {}（{}）".format(stamp, record.emoji, record.title, duration)))
        if record.overview:
            blocks.extend(_paragraphs(record.overview))

    return blocks
