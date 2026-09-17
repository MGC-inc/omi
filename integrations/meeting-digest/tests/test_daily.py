from datetime import date, datetime, timezone
from typing import Any, Dict, List

import pytest

from meeting_digest.config import Config
from meeting_digest.daily import build_daily_summary, local_day_bounds, resolve_day
from meeting_digest.models import MeetingRecord
from meeting_digest.pipeline import run_daily
from meeting_digest.sinks.base import Sink, SinkError
from tests.fixtures import list_item


class FakeClient:
    def __init__(self, items: List[Dict[str, Any]]):
        self._items = items
        self.list_calls: List[Dict[str, Any]] = []

    def list_conversations(self, **kwargs) -> List[Dict[str, Any]]:
        self.list_calls.append(kwargs)
        offset = kwargs.get("offset", 0)
        return self._items[offset : offset + kwargs.get("limit", 25)]

    def get_conversation(self, conversation_id: str, include_transcript: bool = True):
        raise AssertionError("the daily review must not fetch transcripts")


class DailySink(Sink):
    supports_daily = True

    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.summaries = []
        self._fail = fail

    def deliver(self, record):
        raise AssertionError("not used")

    def deliver_daily(self, summary):
        if self._fail:
            raise SinkError("{}: refusing".format(self.name))
        self.summaries.append(summary)


class IngestOnlySink(Sink):
    name = "ingest-only"

    def deliver(self, record):
        raise AssertionError("not used")


def _config(tmp_path, **overrides) -> Config:
    defaults = dict(
        api_key="omi_dev_" + "0" * 32,
        state_path=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "out"),
        utc_offset_hours=9,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _records(*payloads) -> List[MeetingRecord]:
    return [MeetingRecord.from_api(p) for p in payloads]


# --- day boundaries ---------------------------------------------------------


def test_a_local_day_is_not_a_utc_day():
    start, end = local_day_bounds(date(2026, 9, 13), 9)

    # JST midnight is 15:00 UTC the previous day.
    assert start == datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)


def test_a_morning_conversation_belongs_to_its_local_day():
    # 08:00 JST on the 13th is 23:00 UTC on the 12th — a UTC-based cut would
    # file it under the wrong day.
    morning = list_item("morning", "2026-09-12T23:00:00Z")

    summary = build_daily_summary(_records(morning), date(2026, 9, 13), 9)

    assert summary.conversation_count == 1


def test_a_late_night_conversation_does_not_leak_into_the_next_day():
    late = list_item("late", "2026-09-13T14:59:00Z")  # 23:59 JST on the 13th

    assert build_daily_summary(_records(late), date(2026, 9, 13), 9).conversation_count == 1
    assert build_daily_summary(_records(late), date(2026, 9, 14), 9).conversation_count == 0


def test_resolve_day_understands_today_and_yesterday():
    now = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)  # 10:00 JST

    assert resolve_day("today", 9, now=now) == date(2026, 9, 13)
    assert resolve_day("yesterday", 9, now=now) == date(2026, 9, 12)
    assert resolve_day("2026-01-05", 9, now=now) == date(2026, 1, 5)


def test_resolve_day_rejects_nonsense():
    with pytest.raises(ValueError):
        resolve_day("last tuesday", 9)


def test_resolve_day_defaults_to_yesterday():
    now = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)

    assert resolve_day(None, 9, now=now) == date(2026, 9, 12)


# --- roll-up content --------------------------------------------------------


def test_open_actions_are_collected_across_conversations_with_their_source():
    records = _records(
        list_item("a", "2026-09-13T01:00:00Z", title="A社商談"),
        list_item("b", "2026-09-13T05:00:00Z", title="社内定例"),
    )

    summary = build_daily_summary(records, date(2026, 9, 13), 9)
    pairs = summary.open_actions

    assert len(pairs) == 2  # one open item per fixture conversation
    assert {record.title for record, _ in pairs} == {"A社商談", "社内定例"}


def test_dated_commitments_come_before_undated_ones():
    records = _records(list_item("a", "2026-09-13T01:00:00Z"))
    summary = build_daily_summary(records, date(2026, 9, 13), 9)

    dues = [item.due_at for _, item in summary.open_actions]

    assert dues[0] is not None


def test_totals_and_categories():
    records = _records(
        list_item("a", "2026-09-13T01:00:00Z"),
        list_item("b", "2026-09-13T05:00:00Z"),
    )

    summary = build_daily_summary(records, date(2026, 9, 13), 9)

    assert summary.conversation_count == 2
    assert summary.total_minutes == 90  # 45 minutes each
    assert summary.category_counts == [("business", 2)]


def test_conversations_are_ordered_chronologically():
    records = _records(
        list_item("later", "2026-09-13T07:00:00Z"),
        list_item("earlier", "2026-09-13T01:00:00Z"),
    )

    summary = build_daily_summary(records, date(2026, 9, 13), 9)

    assert [r.id for r in summary.conversations] == ["earlier", "later"]


def test_an_empty_day_is_representable():
    summary = build_daily_summary([], date(2026, 9, 13), 9)

    assert summary.is_empty
    assert summary.total_minutes == 0


# --- the run ----------------------------------------------------------------


def test_daily_run_never_spends_the_transcript_budget(tmp_path):
    # FakeClient.get_conversation asserts if called; list must stay transcript-free.
    client = FakeClient([list_item("a", "2026-09-13T01:00:00Z")])
    sink = DailySink("markdown")

    run_daily(_config(tmp_path), client, [sink], date(2026, 9, 13))

    assert all(call["include_transcript"] is False for call in client.list_calls)


def test_daily_run_queries_only_the_target_day(tmp_path):
    client = FakeClient([])

    run_daily(_config(tmp_path), client, [DailySink("markdown")], date(2026, 9, 13))

    call = client.list_calls[0]
    assert call["start_date"] == datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    assert call["end_date"] == datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)


def test_sinks_without_a_daily_view_are_skipped_not_failed(tmp_path):
    client = FakeClient([list_item("a", "2026-09-13T01:00:00Z")])
    daily = DailySink("markdown")

    summary = run_daily(_config(tmp_path), client, [daily, IngestOnlySink()], date(2026, 9, 13))

    assert summary.delivered_to == ["markdown"]
    assert summary.skipped_sinks == ["ingest-only"]
    assert summary.ok


def test_one_failing_daily_sink_does_not_block_the_others(tmp_path):
    client = FakeClient([list_item("a", "2026-09-13T01:00:00Z")])
    good = DailySink("markdown")

    summary = run_daily(_config(tmp_path), client, [DailySink("slack", fail=True), good], date(2026, 9, 13))

    assert good.summaries
    assert summary.delivered_to == ["markdown"]
    assert len(summary.failures) == 1


def test_daily_run_delivers_an_empty_day_rather_than_staying_silent(tmp_path):
    # A quiet day is information; skipping delivery looks like a broken job.
    client = FakeClient([])
    sink = DailySink("markdown")

    summary = run_daily(_config(tmp_path), client, [sink], date(2026, 9, 13))

    assert summary.in_day == 0
    assert sink.summaries and sink.summaries[0].is_empty
