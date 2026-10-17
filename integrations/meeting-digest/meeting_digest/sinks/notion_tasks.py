"""Create one row per open action item in a dedicated Notion tasks database.

A task's authoritative home should be the team's own tracker, not a second
copy living inside the meeting log. So this sink writes the task's own
content there, and cross-references the source conversation by id
(``Omi Conversation ID``) rather than re-stating the meeting around it. The
Slack digest, in turn, links to this database instead of inlining every
item's text once it is configured (see ``SlackSink``'s ``tasks_board_url``).

Idempotency mirrors ``NotionSink``: a synthetic id, ``"{conversation_id}:
{index}"``, is written to ``Omi Action ID`` and checked before a row is
created, so a re-run never duplicates a task.

A task already created here is **never updated again**. Once a person is
tracking it in their own workflow — changing its status, reassigning it — this
sink overwriting that on the next run would fight them. Completion, in
particular, is for the task tracker to record, not this pipeline: an item
Omi later marks ``completed`` simply stops being offered here; the row a
person already created is left exactly as they have it.
"""

from typing import Any, Dict, Optional

import httpx

from ..models import ActionItem, MeetingRecord
from .base import Sink, SinkError

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_RICH_TEXT_CHARS = 2000

# Canonical property keys. property_names maps each to the database's own column.
ACTION_ID_PROPERTY = "Omi Action ID"
CONVERSATION_ID_PROPERTY = "Omi Conversation ID"
STATUS_PROPERTY = "Status"
DUE_PROPERTY = "Due"
OWNER_PROPERTY = "Owner"
CONTEXT_PROPERTY = "Context"
SOURCE_PROPERTY = "Source"
DEFAULT_STATUS = "未着手"


class NotionTasksSink(Sink):
    name = "notion_tasks"
    supports_daily = False

    def __init__(
        self,
        token: str,
        database_id: str,
        timeout_seconds: float = 30.0,
        client: Optional[httpx.Client] = None,
        utc_offset_hours: int = 9,
        property_names: Optional[Dict[str, str]] = None,
        default_status: str = DEFAULT_STATUS,
    ):
        if not token:
            raise ValueError("NotionTasksSink requires an integration token")
        if not database_id:
            raise ValueError("NotionTasksSink requires a database id")
        self._database_id = database_id
        self._utc_offset_hours = utc_offset_hours
        self._property_names = dict(property_names or {})
        self._default_status = default_status
        self._schema: Optional[Dict[str, Any]] = None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._headers = {
            "Authorization": "Bearer {}".format(token),
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @property
    def _action_id_column(self) -> str:
        return self._property_names.get(ACTION_ID_PROPERTY, ACTION_ID_PROPERTY)

    def deliver(self, record: MeetingRecord) -> None:
        open_items = [(index, item) for index, item in enumerate(record.action_items) if not item.completed]
        if not open_items:
            return

        schema = self._database_schema()
        check_duplicates = self._action_id_column in schema

        for index, item in open_items:
            action_id = _action_id(record.id, index)
            if check_duplicates and self._already_present(action_id):
                continue
            self._create_task(record, item, action_id, schema)

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
            raise SinkError("notion_tasks: database {} returned no properties".format(self._database_id))
        if not _title_property_name(properties):
            raise SinkError("notion_tasks: database {} has no title property".format(self._database_id))

        self._schema = properties
        return properties

    def _already_present(self, action_id: str) -> bool:
        payload = self._request(
            "POST",
            "/databases/{}/query".format(self._database_id),
            json={"filter": {"property": self._action_id_column, "rich_text": {"equals": action_id}}, "page_size": 1},
        )
        results = payload.get("results")
        return bool(isinstance(results, list) and results)

    def _create_task(self, record: MeetingRecord, item: ActionItem, action_id: str, schema: Dict[str, Any]) -> None:
        properties: Dict[str, Any] = {}
        title_name = _title_property_name(schema)
        if title_name:
            properties[title_name] = {"title": [{"text": {"content": _clip(item.description or "(内容なし)")}}]}

        optional: Dict[str, Any] = {
            STATUS_PROPERTY: lambda: {"select": {"name": self._default_status}},
            DUE_PROPERTY: lambda: {"date": {"start": item.due_at.isoformat()}} if item.due_at else None,
            OWNER_PROPERTY: (
                lambda: {"rich_text": [{"text": {"content": item.owner_name}}]} if item.owner_name else None
            ),
            CONTEXT_PROPERTY: (
                lambda: {"rich_text": [{"text": {"content": _clip(item.context)}}]} if item.context else None
            ),
            SOURCE_PROPERTY: lambda: {"rich_text": [{"text": {"content": _clip(record.title)}}]},
            CONVERSATION_ID_PROPERTY: (
                lambda: {"rich_text": [{"text": {"content": record.id}}]} if record.id else None
            ),
            ACTION_ID_PROPERTY: lambda: {"rich_text": [{"text": {"content": action_id}}]},
        }
        _apply_optional(properties, schema, optional, self._property_names)

        body = {"parent": {"database_id": self._database_id}, "properties": properties}
        self._request("POST", "/pages", json=body)

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            response = self._client.request(method, NOTION_API + path, headers=self._headers, json=json)
        except httpx.HTTPError as exc:
            raise SinkError("notion_tasks: {} {} failed: {}".format(method, path, type(exc).__name__))

        if response.status_code not in (200, 201):
            raise SinkError(
                "notion_tasks: {} {} returned HTTP {} — {}".format(
                    method, path, response.status_code, _error_message(response)
                )
            )

        try:
            payload = response.json()
        except ValueError:
            raise SinkError("notion_tasks: {} {} returned a non-JSON body".format(method, path))
        if not isinstance(payload, dict):
            raise SinkError("notion_tasks: {} {} returned {}".format(method, path, type(payload).__name__))
        return payload


def _action_id(conversation_id: str, index: int) -> str:
    return "{}:{}".format(conversation_id, index)


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


def _title_property_name(schema: Dict[str, Any]) -> Optional[str]:
    for name, declared in schema.items():
        if isinstance(declared, dict) and declared.get("type") == "title":
            return name
    return None


def _clip(text: Optional[str]) -> str:
    value = text or ""
    return value if len(value) <= MAX_RICH_TEXT_CHARS else value[: MAX_RICH_TEXT_CHARS - 1] + "…"


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
