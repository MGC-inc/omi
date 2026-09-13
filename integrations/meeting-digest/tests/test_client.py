import httpx
import pytest

from meeting_digest.client import OmiApiError, OmiClient, RateLimitedError
from meeting_digest.config import Config
from tests.fixtures import conversation_payload

VALID_KEY = "omi_dev_" + "a" * 32


def _client(handler, **overrides) -> OmiClient:
    config = Config(api_key=VALID_KEY, **overrides)
    return OmiClient(config, transport=httpx.MockTransport(handler), sleep=lambda _seconds: None)


def test_sends_the_key_as_a_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=[])

    with _client(handler) as client:
        client.list_conversations()

    assert seen["auth"] == "Bearer {}".format(VALID_KEY)


def test_list_returns_only_dict_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[conversation_payload(), "garbage", None])

    with _client(handler) as client:
        items = client.list_conversations()

    assert len(items) == 1


def test_include_transcript_is_sent_as_a_query_parameter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=conversation_payload())

    with _client(handler) as client:
        client.get_conversation("conv_001", include_transcript=True)

    assert "include_transcript=true" in seen["url"]


def test_401_is_immediate_and_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"detail": "invalid key"})

    with _client(handler) as client:
        with pytest.raises(OmiApiError) as exc:
            client.list_conversations()

    assert exc.value.status_code == 401
    assert len(calls) == 1


def test_403_names_the_missing_scope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Insufficient permissions."})

    with _client(handler) as client:
        with pytest.raises(OmiApiError) as exc:
            client.list_conversations()

    assert "conversations:read" in str(exc.value)


def test_429_is_retried_then_surfaces_as_rate_limited():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "rate limited"})

    with _client(handler) as client:
        with pytest.raises(RateLimitedError) as exc:
            client.list_conversations()

    assert exc.value.retry_after_seconds == 1.0
    assert len(calls) == 3


def test_a_transient_500_is_retried_and_can_succeed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json=[conversation_payload()])

    with _client(handler) as client:
        items = client.list_conversations()

    assert len(items) == 1
    assert len(calls) == 2


def test_a_404_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"detail": "not found"})

    with _client(handler) as client:
        with pytest.raises(OmiApiError):
            client.get_conversation("missing")

    assert len(calls) == 1
