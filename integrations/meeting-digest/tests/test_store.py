from meeting_digest.models import MeetingRecord
from meeting_digest.store import ConversationStore
from tests.fixtures import conversation_payload


def _store(tmp_path) -> ConversationStore:
    store = ConversationStore(str(tmp_path / "conversations.db"))
    store.initialize()
    return store


def test_initialize_creates_missing_directories(tmp_path):
    store = ConversationStore(str(tmp_path / "nested" / "deeper" / "conversations.db"))
    store.initialize()

    assert store.count() == 0


def test_upsert_is_idempotent(tmp_path):
    store = _store(tmp_path)
    record = MeetingRecord.from_api(conversation_payload())

    store.upsert(record)
    store.upsert(record)

    assert store.count() == 1
    assert len(store.action_items(open_only=False)) == 2


def test_upsert_replaces_rather_than_accumulates_action_items(tmp_path):
    store = _store(tmp_path)
    store.upsert(MeetingRecord.from_api(conversation_payload()))

    revised = conversation_payload()
    revised["structured"]["action_items"] = [{"description": "差し替え後の唯一の項目", "completed": False}]
    store.upsert(MeetingRecord.from_api(revised))

    items = store.action_items(open_only=False)
    assert [item["description"] for item in items] == ["差し替え後の唯一の項目"]


def test_stored_payload_round_trips_through_the_model(tmp_path):
    # The API rebuilds records from the store to reuse the daily roll-up code,
    # so to_dict/from_stored must survive the trip.
    store = _store(tmp_path)
    original = MeetingRecord.from_api(conversation_payload())
    store.upsert(original)

    restored = MeetingRecord.from_stored(store.get(original.id))

    assert restored.title == original.title
    assert restored.started_at == original.started_at
    assert restored.duration_minutes == original.duration_minutes
    assert [i.description for i in restored.action_items] == [i.description for i in original.action_items]
    assert restored.action_items[0].due_at == original.action_items[0].due_at
    assert [u.text for u in restored.transcript] == [u.text for u in original.transcript]


def test_list_can_filter_by_date_range(tmp_path):
    store = _store(tmp_path)
    store.upsert(MeetingRecord.from_api(conversation_payload(conversation_id="old", started_at="2026-09-01T01:00:00Z")))
    store.upsert(MeetingRecord.from_api(conversation_payload(conversation_id="new", started_at="2026-09-20T01:00:00Z")))

    found = store.list(since="2026-09-10T00:00:00+00:00")

    assert [item["id"] for item in found] == ["new"]


def test_get_can_drop_the_transcript(tmp_path):
    store = _store(tmp_path)
    store.upsert(MeetingRecord.from_api(conversation_payload()))

    assert "transcript" not in store.get("conv_001", include_transcript=False)
