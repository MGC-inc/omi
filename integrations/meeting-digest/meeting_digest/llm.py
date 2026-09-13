"""One small interface over the LLM providers this pipeline can use.

Two providers, because the choice is an operating decision rather than a
technical one: Gemini is the default (cheap enough to run over every
conversation), Claude is available for the same calls. Adding a third means
adding a class here and one line in ``build_llm``.

The interface is deliberately two methods. ``complete_text`` returns prose —
that is transcript cleanup. ``complete_json`` returns a validated object — that
is anything the pipeline branches on, and a provider that can enforce a schema
server-side does so rather than being asked nicely in the prompt.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GEMINI = "gemini"
CLAUDE = "claude"

#: Recommended in Google's model list for "general agentic and everyday tasks",
#: which is what curation is. Cheaper tiers exist (gemini-3.5-flash-lite); the
#: README says so rather than this file choosing thrift for the operator.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

MAX_OUTPUT_TOKENS = 16000

#: Substrings identifying a failure every later call would hit too: a missing or
#: rejected key, an unknown model. The caller stops rather than repeating it.
_FATAL_MARKERS = (
    "could not resolve authentication",
    "authentication_error",
    "invalid x-api-key",
    "api key not valid",
    "api_key_invalid",
    "permission_denied",
    "not_found_error",
    "was not found",
    "is not found for api version",
)


class LLMError(RuntimeError):
    """A model call failed. ``fatal`` marks a misconfiguration, not bad luck."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class GeminiClient:
    """Google Gen AI SDK (``google-genai``; the old google-generativeai is deprecated)."""

    name = GEMINI

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_GEMINI_MODEL, client=None):
        self.model = model
        if client is not None:
            self._client = client
            return
        try:
            from google import genai
        except ImportError:
            raise LLMError(
                "the gemini provider needs the google-genai package: pip install -r requirements-llm.txt",
                fatal=True,
            )
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set.", fatal=True)
        self._client = genai.Client(api_key=key)

    def complete_text(self, system: str, user: str) -> str:
        return self._generate(system, user, schema=None)

    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        raw = self._generate(system, user, schema=schema)
        return _parse_json(raw, self.model)

    def _generate(self, system: str, user: str, schema: Optional[Dict[str, Any]]) -> str:
        from google.genai import types

        config: Dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if schema is not None:
            # Enforced by the API, so a malformed object cannot reach the caller.
            config["response_mime_type"] = "application/json"
            config["response_schema"] = schema

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(**config),
            )
        except Exception as exc:
            raise _wrap(exc, self.model)

        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise LLMError("{}: returned no text".format(self.model))
        return text.strip()


class ClaudeClient:
    """Anthropic SDK. Schema enforcement is asked for in the prompt and verified here."""

    name = CLAUDE

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_CLAUDE_MODEL, client=None):
        self.model = model
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError:
            raise LLMError(
                "the claude provider needs the anthropic package: pip install -r requirements-llm.txt",
                fatal=True,
            )
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete_text(self, system: str, user: str) -> str:
        return self._generate(system, user)

    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        instructed = (
            system + "\n\n出力は次の JSON Schema に厳密に従った JSON のみ。前置きもコードフェンスも書かない。\n"
        )
        instructed += json.dumps(schema, ensure_ascii=False)
        return _parse_json(self._generate(instructed, user), self.model)

    def _generate(self, system: str, user: str) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise _wrap(exc, self.model)

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMError("{}: the model declined the request".format(self.model))

        text = "".join(
            block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise LLMError("{}: returned no text".format(self.model))
        return text.strip()


def build_llm(
    provider: str,
    model: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
):
    key = (provider or GEMINI).strip().lower()
    if key == GEMINI:
        return GeminiClient(api_key=gemini_api_key, model=model or DEFAULT_GEMINI_MODEL)
    if key == CLAUDE:
        return ClaudeClient(api_key=anthropic_api_key, model=model or DEFAULT_CLAUDE_MODEL)
    raise LLMError("Unknown LLM provider {!r}. Known providers: gemini, claude.".format(provider), fatal=True)


def _parse_json(raw: str, model: str) -> Dict[str, Any]:
    text = raw.strip()
    # A provider that ignores "no code fences" should not cost us the call.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    try:
        payload = json.loads(text)
    except ValueError:
        raise LLMError("{}: returned text that is not JSON: {}".format(model, text[:200]))
    if not isinstance(payload, dict):
        raise LLMError("{}: returned {} rather than an object".format(model, type(payload).__name__))
    return payload


def _wrap(exc: Exception, model: str) -> LLMError:
    detail = str(exc)[:200]
    lowered = detail.lower()
    fatal = any(marker in lowered for marker in _FATAL_MARKERS) or type(exc).__name__ in (
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
    )
    return LLMError("{} failed: {}: {}".format(model, type(exc).__name__, detail), fatal=fatal)
