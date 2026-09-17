import pytest

from meeting_digest.llm import (
    CLAUDE,
    DEFAULT_GEMINI_MODEL,
    GEMINI,
    ClaudeClient,
    GeminiClient,
    LLMError,
    build_llm,
)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class FakeGeminiModels:
    def __init__(self, responses=None, error=None):
        self._responses = list(responses or [])
        self._error = error
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._error:
            raise self._error
        return FakeGeminiResponse(self._responses.pop(0) if self._responses else '{"ok": true}')


class FakeGeminiClient:
    def __init__(self, responses=None, error=None):
        self.models = FakeGeminiModels(responses, error)


# --- Gemini -----------------------------------------------------------------


def test_gemini_json_calls_enforce_the_schema_server_side():
    # Asking a model nicely for JSON is weaker than the API refusing anything
    # else. Where the provider can enforce it, it must.
    fake = FakeGeminiClient()
    GeminiClient(client=fake).complete_json("sys", "user", SCHEMA)

    config = fake.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == SCHEMA


def test_gemini_text_calls_do_not_request_json():
    fake = FakeGeminiClient(responses=["整えた本文"])

    assert GeminiClient(client=fake).complete_text("sys", "user") == "整えた本文"
    assert fake.models.calls[0]["config"].response_mime_type is None


def test_gemini_passes_the_system_instruction_and_the_model():
    fake = FakeGeminiClient()
    GeminiClient(client=fake, model="gemini-3.5-flash-lite").complete_text("システム", "本文")

    call = fake.models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["config"].system_instruction == "システム"
    assert call["contents"] == "本文"


def test_the_default_model_is_the_everyday_flash_tier():
    assert DEFAULT_GEMINI_MODEL == "gemini-3.6-flash"


def test_a_code_fenced_answer_still_parses():
    # Providers wrap JSON in fences despite instructions; that should not cost
    # the call.
    fake = FakeGeminiClient(responses=['```json\n{"ok": true}\n```'])

    assert GeminiClient(client=fake).complete_json("sys", "user", SCHEMA) == {"ok": True}


def test_a_non_json_answer_is_an_error():
    fake = FakeGeminiClient(responses=["申し訳ありませんが"])

    with pytest.raises(LLMError):
        GeminiClient(client=fake).complete_json("sys", "user", SCHEMA)


def test_a_json_array_is_rejected_because_callers_expect_an_object():
    fake = FakeGeminiClient(responses=["[1, 2, 3]"])

    with pytest.raises(LLMError):
        GeminiClient(client=fake).complete_json("sys", "user", SCHEMA)


def test_an_empty_answer_is_an_error_not_an_empty_string():
    fake = FakeGeminiClient(responses=["   "])

    with pytest.raises(LLMError):
        GeminiClient(client=fake).complete_text("sys", "user")


def test_a_rejected_key_is_fatal():
    fake = FakeGeminiClient(error=RuntimeError("400 API key not valid. Please pass a valid API key."))

    with pytest.raises(LLMError) as exc:
        GeminiClient(client=fake).complete_text("sys", "user")

    assert exc.value.fatal


def test_an_unknown_model_is_fatal():
    fake = FakeGeminiClient(error=RuntimeError("models/gemini-9 is not found for API version v1"))

    with pytest.raises(LLMError) as exc:
        GeminiClient(client=fake).complete_text("sys", "user")

    assert exc.value.fatal


def test_a_connection_reset_is_not_fatal():
    fake = FakeGeminiClient(error=RuntimeError("connection reset by peer"))

    with pytest.raises(LLMError) as exc:
        GeminiClient(client=fake).complete_text("sys", "user")

    assert not exc.value.fatal


def test_the_error_carries_the_provider_detail():
    fake = FakeGeminiClient(error=RuntimeError("connection reset by peer"))

    with pytest.raises(LLMError) as exc:
        GeminiClient(client=fake).complete_text("sys", "user")

    assert "connection reset by peer" in str(exc.value)


# --- Claude -----------------------------------------------------------------


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeClaudeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeClaudeMessages:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0) if self._responses else FakeClaudeResponse("ok")


class FakeClaudeClient:
    def __init__(self, responses=None):
        self.messages = FakeClaudeMessages(responses)


def test_claude_states_the_schema_in_the_prompt_since_it_cannot_enforce_it():
    fake = FakeClaudeClient(responses=[FakeClaudeResponse('{"ok": true}')])

    ClaudeClient(client=fake).complete_json("システム", "本文", SCHEMA)

    system = fake.messages.calls[0]["system"]
    assert "JSON Schema" in system
    assert '"ok"' in system


def test_a_claude_refusal_is_surfaced():
    fake = FakeClaudeClient(responses=[FakeClaudeResponse("", stop_reason="refusal")])

    with pytest.raises(LLMError):
        ClaudeClient(client=fake).complete_text("sys", "user")


# --- provider selection -----------------------------------------------------


def test_build_llm_selects_by_provider_name():
    assert build_llm(GEMINI, gemini_api_key="x").name == GEMINI
    assert build_llm(CLAUDE, anthropic_api_key="x").name == CLAUDE


def test_the_provider_name_is_case_insensitive():
    assert build_llm("Gemini", gemini_api_key="x").name == GEMINI


def test_an_unknown_provider_is_fatal():
    with pytest.raises(LLMError) as exc:
        build_llm("mystery-model")

    assert exc.value.fatal


def test_gemini_without_a_key_is_fatal_at_construction(monkeypatch):
    # Caught before the first conversation rather than on every one.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(LLMError) as exc:
        build_llm(GEMINI, gemini_api_key=None)

    assert exc.value.fatal
    assert "GEMINI_API_KEY" in str(exc.value)


def test_gemini_falls_back_to_the_environment_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-environment")

    assert build_llm(GEMINI, gemini_api_key=None).name == GEMINI
