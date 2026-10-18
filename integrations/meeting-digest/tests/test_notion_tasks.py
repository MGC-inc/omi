import json
from typing import Any, Dict, List

import httpx
import pytest

from meeting_digest.models import MeetingRecord
from meeting_digest.sinks.base import SinkError
from meeting_digest.sinks.notion_tasks import NotionTasksSink
from tests.fixtures import conversation_payload

DATABASE_ID = "c" * 32

FULL_SCHEMA = {
    "Name": {"type": "title", "title": {}},
    "Status": {"type": "select", "select": {}},
    "Due": {"type": "date", "date": {}},
    "Owner": {"type": "rich_text", "rich_text": {}},
    "Context": {"type": "rich_text", "rich_text": {}},
    "Source": {"type": "rich_text", "rich_text": {}},
    "Omi Conversation ID": {"type": "rich_text", "rich_text": {}},
    "Omi Action ID": {"type": "rich_text", "rich_text": {}},
}

MINIMAL_SCHEMA = {"Name": {"type": "title", "title": {}}}


class NotionStub:
    def __init__(self, schema: Dict[str, Any], existing_action_ids: List[str] = None):
        self.schema = schema
        self.existing_action_ids = existing_action_ids or []
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
            hits = [{"id": "page_existing"}] if wanted in self.existing_action_ids else []
            return httpx.Response(200, json={"results": hits})
        if request.method == "POST" and path == "/v1/pages":
            return httpx.Response(200, json={"id": "page_new"})
        return httpx.Response(404, json={"message": "unexpected {} {}".format(request.method, path)})

    def calls(self, method: str, fragment: str) -> List[Dict[str, Any]]:
        return [
            body
            for request, body in zip(self.requests, self.bodies)
            if request.method == method and fragment in request.url.path
        ]


def _sink(stub: NotionStub, **kwargs) -> NotionTasksSink:
    return NotionTasksSink("ntn_test", DATABASE_ID, client=httpx.Client(transport=httpx.MockTransport(stub)), **kwargs)


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


def test_creates_one_task_per_open_action_item():
    # The fixture has two action items, one already completed.
    stub = NotionStub(FULL_SCHEMA)
    _sink(stub).deliver(_record())

    created = stub.calls("POST", "/pages")

    assert len(created) == 1
    properties = created[0]["properties"]
    assert properties["Name"]["title"][0]["text"]["content"] == "見積書を送付する"
    assert properties["Status"]["select"]["name"] == "未着手"
    assert properties["Due"]["date"]["start"].startswith("2026-09-15")
    assert properties["Owner"]["rich_text"][0]["text"]["content"] == "松尾"
    assert properties["Context"]["rich_text"][0]["text"]["content"] == "先方の稟議が月内締め"
    assert properties["Source"]["rich_text"][0]["text"]["content"] == "A社との商談"
    assert properties["Omi Conversation ID"]["rich_text"][0]["text"]["content"] == "conv_001"
    assert properties["Omi Action ID"]["rich_text"][0]["text"]["content"] == "conv_001:0"


def test_completed_items_are_never_turned_into_tasks():
    stub = NotionStub(FULL_SCHEMA)
    _sink(stub).deliver(_record())

    created = stub.calls("POST", "/pages")[0]
    assert "議事録を共有する" not in json.dumps(created, ensure_ascii=False)


def test_a_conversation_with_no_open_items_makes_no_requests():
    payload = conversation_payload()
    for item in payload["structured"]["action_items"]:
        item["completed"] = True
    stub = NotionStub(FULL_SCHEMA)

    _sink(stub).deliver(MeetingRecord.from_api(payload))

    assert stub.requests == []


def test_an_existing_task_is_not_duplicated():
    stub = NotionStub(FULL_SCHEMA, existing_action_ids=["conv_001:0"])

    _sink(stub).deliver(_record())

    assert stub.calls("POST", "/pages") == []


def test_without_an_action_id_column_no_duplicate_query_is_made():
    stub = NotionStub(MINIMAL_SCHEMA)

    _sink(stub).deliver(_record())

    assert stub.calls("POST", "/query") == []
    assert len(stub.calls("POST", "/pages")) == 1


def test_a_title_only_database_still_works():
    stub = NotionStub(MINIMAL_SCHEMA)
    _sink(stub).deliver(_record())

    properties = stub.calls("POST", "/pages")[0]["properties"]
    assert list(properties) == ["Name"]


def test_property_names_can_be_mapped_to_the_databases_own_columns():
    schema = {
        "タスク": {"type": "title", "title": {}},
        "状態": {"type": "select", "select": {}},
        "Omi Action ID": {"type": "rich_text", "rich_text": {}},
    }
    stub = NotionStub(schema)

    _sink(stub, property_names={"Status": "状態"}).deliver(_record())

    properties = stub.calls("POST", "/pages")[0]["properties"]
    assert properties["タスク"]["title"][0]["text"]["content"] == "見積書を送付する"
    assert properties["状態"]["select"]["name"] == "未着手"


def test_a_notion_error_becomes_a_sink_error_with_its_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Could not find database with ID"})

    sink = NotionTasksSink("ntn_test", DATABASE_ID, client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(SinkError) as exc:
        sink.deliver(_record())

    assert "Could not find database" in str(exc.value)


def test_an_item_without_a_due_date_or_owner_still_creates_a_task():
    payload = conversation_payload()
    payload["structured"]["action_items"] = [{"description": "次のアクション", "completed": False}]
    stub = NotionStub(FULL_SCHEMA)

    _sink(stub).deliver(MeetingRecord.from_api(payload))

    properties = stub.calls("POST", "/pages")[0]["properties"]
    assert "Due" not in properties
    assert "Owner" not in properties
