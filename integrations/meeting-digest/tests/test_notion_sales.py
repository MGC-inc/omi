import json
from typing import Any, Dict, List

import httpx

from meeting_digest.curate import Curation
from meeting_digest.models import MeetingRecord
from meeting_digest.sinks.notion_sales import NotionSalesSink
from tests.fixtures import conversation_payload

DATABASE_ID = "b" * 32

MINIMAL_SCHEMA = {"Name": {"type": "title", "title": {}}}


class NotionStub:
    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
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
            return httpx.Response(200, json={"results": []})
        if request.method == "POST" and path == "/v1/pages":
            return httpx.Response(200, json={"id": "page_new"})
        return httpx.Response(404, json={"message": "unexpected {} {}".format(request.method, path)})

    def calls(self, method: str, fragment: str) -> List[Dict[str, Any]]:
        return [
            body
            for request, body in zip(self.requests, self.bodies)
            if request.method == method and fragment in request.url.path
        ]


def _sink(stub: NotionStub) -> NotionSalesSink:
    return NotionSalesSink("ntn_test", DATABASE_ID, client=httpx.Client(transport=httpx.MockTransport(stub)))


def _record(sales: bool = False, **kwargs) -> MeetingRecord:
    record = MeetingRecord.from_api(conversation_payload(**kwargs))
    if sales:
        record = record.with_curation(Curation.from_clip("営業メモ", sales=True))
    return record


def test_an_untagged_conversation_is_a_silent_no_op():
    stub = NotionStub(MINIMAL_SCHEMA)

    _sink(stub).deliver(_record(sales=False))

    assert stub.calls("POST", "/pages") == []


def test_a_sales_tagged_conversation_is_written():
    stub = NotionStub(MINIMAL_SCHEMA)

    _sink(stub).deliver(_record(sales=True))

    assert len(stub.calls("POST", "/pages")) == 1


def test_the_transcript_is_excluded_even_if_requested():
    # NotionSalesSink hardcodes include_transcript=False — a knowledge
    # database holds ideas, not a second transcript archive.
    stub = NotionStub(MINIMAL_SCHEMA)

    sink = NotionSalesSink(
        "ntn_test",
        DATABASE_ID,
        client=httpx.Client(transport=httpx.MockTransport(stub)),
        include_transcript=True,
    )
    sink.deliver(_record(sales=True))

    body = json.dumps(stub.calls("POST", "/pages")[0], ensure_ascii=False)
    assert "本日はお時間をいただきありがとうございます。" not in body


def test_daily_review_is_not_supported():
    # A day's review mixes every conversation; writing it here would defeat
    # the point of a sales-only destination.
    assert NotionSalesSink.supports_daily is False
