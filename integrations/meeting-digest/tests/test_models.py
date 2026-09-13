from datetime import datetime, timezone

from meeting_digest.models import MeetingRecord, parse_timestamp
from tests.fixtures import conversation_payload


def test_normalizes_a_full_conversation():
    record = MeetingRecord.from_api(conversation_payload())

    assert record.id == "conv_001"
    assert record.title == "A社との商談"
    assert record.category == "business"
    assert record.language == "ja"
    assert record.duration_minutes == 45
    assert [s.heading for s in record.sections] == ["先方の要望"]
    assert len(record.action_items) == 2
    assert len(record.transcript) == 2
    assert record.has_transcript


def test_open_action_items_excludes_completed_ones():
    record = MeetingRecord.from_api(conversation_payload())

    assert [item.description for item in record.open_action_items] == ["見積書を送付する"]


def test_action_item_carries_owner_and_due_date():
    record = MeetingRecord.from_api(conversation_payload())
    item = record.open_action_items[0]

    assert item.owner_name == "松尾"
    assert item.due_at == datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
    assert item.context == "先方の稟議が月内締め"


def test_missing_structured_block_does_not_raise():
    record = MeetingRecord.from_api({"id": "conv_x", "created_at": "2026-09-10T01:00:00Z"})

    assert record.id == "conv_x"
    assert record.title == "(untitled)"
    assert record.overview == ""
    assert record.action_items == []
    assert record.transcript == []
    assert record.duration_minutes is None


def test_unknown_upstream_fields_are_ignored():
    payload = conversation_payload()
    payload["some_new_upstream_field"] = {"nested": True}
    payload["structured"]["another_new_field"] = 42

    record = MeetingRecord.from_api(payload)

    assert record.title == "A社との商談"


def test_malformed_timestamp_becomes_none_instead_of_failing():
    payload = conversation_payload()
    payload["started_at"] = "not-a-timestamp"

    record = MeetingRecord.from_api(payload)

    assert record.started_at is None
    # occurred_at still resolves, via created_at.
    assert record.occurred_at == datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)


def test_parse_timestamp_accepts_the_trailing_z_the_api_emits():
    # Python 3.9's datetime.fromisoformat rejects 'Z' outright; the API sends it.
    assert parse_timestamp("2026-09-10T01:00:00Z") == datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)


def test_parse_timestamp_assumes_utc_for_naive_values():
    assert parse_timestamp("2026-09-10T01:00:00") == datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)


def test_transcript_segments_absent_means_no_transcript():
    record = MeetingRecord.from_api(conversation_payload(with_transcript=False))

    assert not record.has_transcript
