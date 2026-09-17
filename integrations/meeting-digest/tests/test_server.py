import pytest
from fastapi.testclient import TestClient

from meeting_digest.config import Config
from meeting_digest.models import MeetingRecord
from meeting_digest.server import ServerConfigError, create_app
from meeting_digest.store import ConversationStore
from tests.fixtures import conversation_payload

TOKEN = "t" * 40
AUTH = {"Authorization": "Bearer " + TOKEN}


def _config(tmp_path, **overrides) -> Config:
    defaults = dict(
        api_key="omi_dev_" + "0" * 32,
        store_path=str(tmp_path / "conversations.db"),
        api_server_token=TOKEN,
        utc_offset_hours=9,
    )
    defaults.update(overrides)
    return Config(**defaults)


@pytest.fixture
def client(tmp_path):
    config = _config(tmp_path)
    store = ConversationStore(config.store_path)
    store.initialize()
    store.upsert(MeetingRecord.from_api(conversation_payload()))
    store.upsert(
        MeetingRecord.from_api(
            conversation_payload(
                conversation_id="conv_002",
                started_at="2026-09-13T01:00:00Z",
                title="社内定例",
            )
        )
    )
    return TestClient(create_app(config, store=store))


# --- fail-closed startup ----------------------------------------------------


def test_refuses_to_start_without_a_token(tmp_path):
    # No unauthenticated window is ever opened over conversation content.
    with pytest.raises(ServerConfigError) as exc:
        create_app(_config(tmp_path, api_server_token=None))

    assert "MD_API_TOKEN" in str(exc.value)


def test_refuses_a_short_token(tmp_path):
    with pytest.raises(ServerConfigError):
        create_app(_config(tmp_path, api_server_token="short"))


# --- authentication ---------------------------------------------------------


def test_conversations_require_a_token(client):
    assert client.get("/v1/conversations").status_code == 401


def test_a_wrong_token_is_rejected(client):
    response = client.get("/v1/conversations", headers={"Authorization": "Bearer " + "x" * 40})

    assert response.status_code == 401


def test_health_is_open_and_carries_no_content(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert set(response.json()) == {"status", "version"}


# --- reads ------------------------------------------------------------------


def test_lists_conversations_newest_first(client):
    body = client.get("/v1/conversations", headers=AUTH).json()

    assert body["count"] == 2
    assert body["items"][0]["id"] == "conv_002"


def test_list_omits_transcripts_by_default(client):
    body = client.get("/v1/conversations", headers=AUTH).json()

    assert "transcript" not in body["items"][0]


def test_a_single_conversation_includes_its_transcript(client):
    body = client.get("/v1/conversations/conv_001", headers=AUTH).json()

    assert body["title"] == "A社との商談"
    assert len(body["transcript"]) == 2
    assert body["transcript"][0]["is_user"] is True


def test_an_unknown_conversation_is_a_404(client):
    assert client.get("/v1/conversations/nope", headers=AUTH).status_code == 404


def test_search_matches_content(client):
    body = client.get("/v1/conversations", headers=AUTH, params={"q": "社内定例"}).json()

    assert [item["id"] for item in body["items"]] == ["conv_002"]


def test_action_items_default_to_open_ones_only(client):
    body = client.get("/v1/action-items", headers=AUTH).json()

    assert body["count"] == 2  # one open item per stored conversation
    assert all(item["completed"] is False for item in body["items"])
    assert body["items"][0]["conversation"]["id"]


def test_action_items_can_include_completed_ones(client):
    body = client.get("/v1/action-items", headers=AUTH, params={"open_only": False}).json()

    assert body["count"] == 4


def test_daily_rollup_uses_the_local_day(client):
    # conv_002 starts 01:00 UTC on the 13th = 10:00 JST on the 13th.
    body = client.get("/v1/daily/2026-09-13", headers=AUTH).json()

    assert body["conversation_count"] == 1
    assert body["conversations"][0]["id"] == "conv_002"
    assert body["total_minutes"] == 45
    assert body["open_actions"][0]["conversation"]["id"] == "conv_002"


def test_daily_rejects_a_malformed_date(client):
    assert client.get("/v1/daily/13-09-2026", headers=AUTH).status_code == 400


def test_stats_reports_what_is_stored(client):
    body = client.get("/v1/stats", headers=AUTH).json()

    assert body["conversations"] == 2
