"""A stand-in for llm.py's clients, shared by the refinement and curation tests."""

import json
from typing import Any, Dict, List, Optional

from meeting_digest.llm import LLMError


class FakeLLM:
    """Records every call; returns queued answers, or a default."""

    name = "fake"
    model = "fake-model"

    def __init__(
        self,
        text_responses: Optional[List[str]] = None,
        json_responses: Optional[List[Dict[str, Any]]] = None,
        error: Optional[Exception] = None,
    ):
        self._text = list(text_responses or [])
        self._json = list(json_responses or [])
        self._error = error
        self.calls: List[Dict[str, Any]] = []

    def complete_text(self, system: str, user: str) -> str:
        self.calls.append({"kind": "text", "system": system, "user": user})
        if self._error:
            raise self._error
        return self._text.pop(0) if self._text else "整形済みテキスト"

    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"kind": "json", "system": system, "user": user, "schema": schema})
        if self._error:
            raise self._error
        if self._json:
            return self._json.pop(0)
        return {"worth_keeping": True, "reason": "既定", "ideas": [], "topics": []}


def fatal(message: str = "API key not valid") -> LLMError:
    return LLMError(message, fatal=True)


def transient(message: str = "connection reset by peer") -> LLMError:
    return LLMError(message, fatal=False)
