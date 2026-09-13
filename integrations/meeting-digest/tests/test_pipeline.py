from typing import Any, Dict, List, Optional

from meeting_digest.client import OmiApiError, RateLimitedError
from meeting_digest.config import Config
from meeting_digest.models import MeetingRecord
from meeting_digest.pipeline import run
from meeting_digest.sinks.base import Sink, SinkError
from meeting_digest.state import DeliveryState
from tests.fixtures import conversation_payload, list_item


class FakeClient:
    def __init__(self, items: List[Dict[str, Any]], fail_on: Optional[Dict[str, Exception]] = None):
        self._items = items
        self._fail_on = fail_on or {}
        self.list_calls: List[Dict[str, Any]] = []
        self.fetched_ids: List[str] = []

    def list_conversations(self, **kwargs) -> List[Dict[str, Any]]:
        self.list_calls.append(kwargs)
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 25)
        return self._items[offset : offset + limit]

    def get_conversation(self, conversation_id: str, include_transcript: bool = True) -> Dict[str, Any]:
        if conversation_id in self._fail_on:
            raise self._fail_on[conversation_id]
        self.fetched_ids.append(conversation_id)
        return conversation_payload(conversation_id=conversation_id, with_transcript=include_transcript)


class RecordingSink(Sink):
    def __init__(self, name: str, fail_ids: Optional[List[str]] = None):
        self.name = name
        self.delivered: List[str] = []
        self._fail_ids = set(fail_ids or [])

    def deliver(self, record: MeetingRecord) -> None:
        if record.id in self._fail_ids:
            raise SinkError("{}: refusing {}".format(self.name, record.id))
        self.delivered.append(record.id)


def _config(tmp_path, **overrides) -> Config:
    defaults = dict(
        api_key="omi_dev_" + "0" * 32,
        state_path=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "out"),
        lookback_hours=24,
        list_page_size=50,
        max_transcript_fetches=20,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _items(count: int) -> List[Dict[str, Any]]:
    return [list_item("conv_{}".format(i), "2026-09-10T0{}:00:00Z".format(i % 10)) for i in range(count)]


def test_delivers_every_new_conversation_once(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_items(3))
    sink = RecordingSink("markdown")
    state = DeliveryState.load(config.state_path)

    summary = run(config, client, [sink], state)

    assert summary.listed == 3
    assert summary.delivered == 3
    assert sorted(sink.delivered) == ["conv_0", "conv_1", "conv_2"]


def test_second_run_delivers_nothing_new(tmp_path):
    config = _config(tmp_path)
    items = _items(3)
    first_sink = RecordingSink("markdown")
    run(config, FakeClient(items), [first_sink], DeliveryState.load(config.state_path))

    second_client = FakeClient(items)
    second_sink = RecordingSink("markdown")
    summary = run(config, second_client, [second_sink], DeliveryState.load(config.state_path))

    assert second_sink.delivered == []
    assert summary.already_delivered == 3
    # Nothing already delivered costs a transcript read.
    assert second_client.fetched_ids == []


def test_listing_never_requests_transcripts(tmp_path):
    # Transcript-bearing reads are capped at 25/hour; the list call must stay in
    # the cheap bucket or a routine run can exhaust the budget on its own.
    config = _config(tmp_path)
    client = FakeClient(_items(2))

    run(config, client, [RecordingSink("markdown")], DeliveryState.load(config.state_path))

    assert all(call["include_transcript"] is False for call in client.list_calls)


def test_transcript_budget_defers_the_remainder(tmp_path):
    config = _config(tmp_path, max_transcript_fetches=2)
    client = FakeClient(_items(5))
    sink = RecordingSink("markdown")

    summary = run(config, client, [sink], DeliveryState.load(config.state_path))

    assert summary.fetched == 2
    assert summary.delivered == 2
    assert summary.deferred == 3


def test_deferred_conversations_are_picked_up_next_run(tmp_path):
    config = _config(tmp_path, max_transcript_fetches=2)
    items = _items(5)
    run(config, FakeClient(items), [RecordingSink("markdown")], DeliveryState.load(config.state_path))

    second_sink = RecordingSink("markdown")
    summary = run(config, FakeClient(items), [second_sink], DeliveryState.load(config.state_path))

    assert summary.delivered == 2
    assert summary.already_delivered == 2
    assert len(second_sink.delivered) == 2


def test_rate_limit_stops_the_run_but_keeps_what_was_delivered(tmp_path):
    config = _config(tmp_path)
    items = _items(3)
    client = FakeClient(items, fail_on={"conv_1": RateLimitedError("budget spent")})
    sink = RecordingSink("markdown")
    state = DeliveryState.load(config.state_path)

    summary = run(config, client, [sink], state)

    assert summary.rate_limited
    assert summary.delivered == 1
    # The successful delivery survived the abort.
    assert DeliveryState.load(config.state_path).is_delivered("conv_0", "markdown")


def test_a_failing_sink_does_not_block_the_others(tmp_path):
    config = _config(tmp_path)
    good = RecordingSink("markdown")
    bad = RecordingSink("slack", fail_ids=["conv_0"])

    summary = run(config, FakeClient(_items(1)), [good, bad], DeliveryState.load(config.state_path))

    assert good.delivered == ["conv_0"]
    assert bad.delivered == []
    assert len(summary.failures) == 1


def test_only_the_failed_sink_is_retried_next_run(tmp_path):
    config = _config(tmp_path)
    items = _items(1)
    run(
        config,
        FakeClient(items),
        [RecordingSink("markdown"), RecordingSink("slack", fail_ids=["conv_0"])],
        DeliveryState.load(config.state_path),
    )

    good = RecordingSink("markdown")
    recovered = RecordingSink("slack")
    run(config, FakeClient(items), [good, recovered], DeliveryState.load(config.state_path))

    assert good.delivered == []
    assert recovered.delivered == ["conv_0"]


def test_a_fetch_failure_is_recorded_and_the_run_continues(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_items(3), fail_on={"conv_1": OmiApiError("gone", 404)})
    sink = RecordingSink("markdown")

    summary = run(config, client, [sink], DeliveryState.load(config.state_path))

    assert sorted(sink.delivered) == ["conv_0", "conv_2"]
    assert len(summary.failures) == 1
    assert not summary.ok


def test_backlog_is_processed_oldest_first(tmp_path):
    config = _config(tmp_path, max_transcript_fetches=2)
    items = [
        list_item("newest", "2026-09-10T09:00:00Z"),
        list_item("oldest", "2026-09-10T01:00:00Z"),
        list_item("middle", "2026-09-10T05:00:00Z"),
    ]
    sink = RecordingSink("markdown")

    run(config, FakeClient(items), [sink], DeliveryState.load(config.state_path))

    assert sink.delivered == ["oldest", "middle"]


def test_unexpected_sink_exception_does_not_take_down_the_run(tmp_path):
    class ExplodingSink(Sink):
        name = "exploding"

        def deliver(self, record):
            raise ValueError("boom")

    config = _config(tmp_path)
    good = RecordingSink("markdown")

    summary = run(config, FakeClient(_items(1)), [ExplodingSink(), good], DeliveryState.load(config.state_path))

    assert good.delivered == ["conv_0"]
    assert len(summary.failures) == 1
