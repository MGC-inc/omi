import json
from typing import Any, Dict, List

import httpx
import pytest

from meeting_digest.models import MeetingRecord
from meeting_digest.sinks.base import SinkError
from meeting_digest.sinks.notion import MAX_BLOCKS_PER_REQUEST, MAX_RICH_TEXT_CHARS, NotionSink, build_blocks
from tests.fixtures import conversation_payload

DATABASE_ID = "a" * 32

FULL_SCHEMA = {
    "Name": {"type": "title", "title": {}},
    "Date": {"type": "date", "date": {}},
    "Category": {"type": "select", "select": {}},
    "Duration (min)": {"type": "number", "number": {}},
    "Open Actions": {"type": "number", "number": {}},
    "Language": {"type": "select", "select": {}},
    "Omi ID": {"type": "rich_text", "rich_text": {}},
}

MINIMAL_SCHEMA = {"Name": {"type": "title", "title": {}}}


class NotionStub:
    """Records every request and answers like the Notion API."""

    def __init__(self, schema: Dict[str, Any], existing_ids: List[str] = None):
        self.schema = schema
        self.existing_ids = existing_ids or []
        self.requests: List[httpx.Request] = []
        self.bodies: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else {}
        self.bodies.append(body)
        path = request.url.path

        if request.method == "GET" and path.startswith("/v1/databases/"):
            return httpx.Response(200, json={"properties": self.schema})
        if request.method == "POST" and path.endswith("/query"):
            wanted = body["filter"]["rich_text"]["equals"]
            hits = [{"id": "page_existing"}] if wanted in self.existing_ids else []
            return httpx.Response(200, json={"results": hits})
        if request.method == "POST" and path == "/v1/pages":
            return httpx.Response(200, json={"id": "page_new"})
        if request.method == "PATCH" and "/children" in path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"message": "unexpected {} {}".format(request.method, path)})

    def calls(self, method: str, fragment: str) -> List[Dict[str, Any]]:
        return [
            body
            for request, body in zip(self.requests, self.bodies)
            if request.method == method and fragment in request.url.path
        ]


def _sink(stub: NotionStub, **kwargs) -> NotionSink:
    return NotionSink("ntn_test", DATABASE_ID, client=httpx.Client(transport=httpx.MockTransport(stub)), **kwargs)


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


def test_creates_a_page_with_every_declared_property():
    stub = NotionStub(FULL_SCHEMA)
    _sink(stub).deliver(_record())

    properties = stub.calls("POST", "/pages")[0]["properties"]

    assert properties["Name"]["title"][0]["text"]["content"] == "A社との商談"
    assert properties["Date"]["date"]["start"].startswith("2026-09-10T01:00")
    assert properties["Category"]["select"]["name"] == "business"
    assert properties["Duration (min)"]["number"] == 45
    assert properties["Open Actions"]["number"] == 1
    assert properties["Omi ID"]["rich_text"][0]["text"]["content"] == "conv_001"


def test_a_title_only_database_still_works():
    # Writing a property the database does not declare is a 400 from Notion, so
    # a minimal database must receive only its title.
    stub = NotionStub(MINIMAL_SCHEMA)
    _sink(stub).deliver(_record())

    properties = stub.calls("POST", "/pages")[0]["properties"]

    assert list(properties) == ["Name"]


def test_the_title_property_may_carry_any_name():
    stub = NotionStub({"議題": {"type": "title", "title": {}}})
    _sink(stub).deliver(_record())

    assert "議題" in stub.calls("POST", "/pages")[0]["properties"]


def test_a_property_whose_type_changed_is_skipped():
    schema = dict(MINIMAL_SCHEMA)
    schema["Category"] = {"type": "rich_text", "rich_text": {}}  # was a select
    stub = NotionStub(schema)

    _sink(stub).deliver(_record())

    assert "Category" not in stub.calls("POST", "/pages")[0]["properties"]


def test_an_existing_page_is_not_duplicated():
    stub = NotionStub(FULL_SCHEMA, existing_ids=["conv_001"])

    _sink(stub).deliver(_record())

    assert stub.calls("POST", "/pages") == []


def test_without_an_omi_id_column_no_duplicate_query_is_made():
    stub = NotionStub(MINIMAL_SCHEMA)

    _sink(stub).deliver(_record())

    assert stub.calls("POST", "/query") == []
    assert len(stub.calls("POST", "/pages")) == 1


def test_the_database_schema_is_read_once_across_deliveries():
    stub = NotionStub(MINIMAL_SCHEMA)
    sink = _sink(stub)

    sink.deliver(_record(conversation_id="conv_1"))
    sink.deliver(_record(conversation_id="conv_2"))

    assert len(stub.calls("GET", "/databases/")) == 1


def test_blocks_beyond_the_first_hundred_are_appended():
    # Notion caps a page create at 100 children; the rest go in batches.
    payload = conversation_payload()
    payload["transcript_segments"] = [
        {
            "id": "s{}".format(i),
            "text": "発言 {}".format(i),
            "speaker": "SPEAKER_00",
            "is_user": False,
            "start": float(i),
            "end": float(i) + 1,
        }
        for i in range(250)
    ]
    stub = NotionStub(MINIMAL_SCHEMA)

    _sink(stub).deliver(MeetingRecord.from_api(payload))

    created = stub.calls("POST", "/pages")[0]
    appended = stub.calls("PATCH", "/children")

    assert len(created["children"]) == MAX_BLOCKS_PER_REQUEST
    assert appended, "the remainder must be appended"
    assert all(len(call["children"]) <= MAX_BLOCKS_PER_REQUEST for call in appended)


def test_long_text_is_split_into_valid_rich_text_runs():
    payload = conversation_payload()
    payload["structured"]["overview"] = "あ" * (MAX_RICH_TEXT_CHARS * 2 + 10)

    blocks = build_blocks(MeetingRecord.from_api(payload))

    for block in blocks:
        for run in block.get(block["type"], {}).get("rich_text", []):
            assert len(run["text"]["content"]) <= MAX_RICH_TEXT_CHARS


def test_action_items_become_checkboxes_reflecting_completion():
    blocks = build_blocks(_record())
    todos = [b for b in blocks if b["type"] == "to_do"]

    assert len(todos) == 2
    assert todos[0]["to_do"]["checked"] is False
    assert "見積書を送付する" in todos[0]["to_do"]["rich_text"][0]["text"]["content"]
    assert todos[1]["to_do"]["checked"] is True


def test_transcript_can_be_excluded():
    blocks = build_blocks(_record(), include_transcript=False)
    text = json.dumps(blocks, ensure_ascii=False)

    assert "文字起こし" not in text
    assert "本日はお時間をいただきありがとうございます。" not in text


def test_sends_the_required_notion_headers():
    stub = NotionStub(MINIMAL_SCHEMA)
    _sink(stub).deliver(_record())

    request = stub.requests[0]
    assert request.headers["Authorization"] == "Bearer ntn_test"
    assert request.headers["Notion-Version"] == "2022-06-28"


def test_a_notion_error_becomes_a_sink_error_with_its_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Could not find database with ID"})

    sink = NotionSink("ntn_test", DATABASE_ID, client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(SinkError) as exc:
        sink.deliver(_record())

    assert "Could not find database" in str(exc.value)


def test_a_database_without_a_title_property_is_rejected_clearly():
    stub = NotionStub({"Notes": {"type": "rich_text", "rich_text": {}}})

    with pytest.raises(SinkError) as exc:
        _sink(stub).deliver(_record())

    assert "no title property" in str(exc.value)


def test_notion_event_line_uses_local_time():
    blocks = build_blocks(_record(), utc_offset_hours=9)
    text = json.dumps(blocks, ensure_ascii=False)

    # The fixture event starts 05:00 UTC = 14:00 JST.
    assert "2026-09-20 14:00 UTC+9" in text


def test_notion_due_date_uses_local_time():
    blocks = build_blocks(_record(), utc_offset_hours=9)
    todos = [b for b in blocks if b["type"] == "to_do"]

    assert "期限: 2026-09-15" in todos[0]["to_do"]["rich_text"][0]["text"]["content"]
