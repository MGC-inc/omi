"""Thin, read-only client for the Omi Developer API.

Only the two GET endpoints this pipeline needs are exposed. Keeping write verbs
out of the client means a key that was accidentally issued with write scopes
still cannot be used to mutate anything from here.

Rate limits enforced upstream (backend/utils/rate_limit_config.py), per key per
hour:

* ``dev:conversation_reads_total``       60  — every conversation read
* ``dev:conversations_read``             60  — the list endpoint
* ``dev:conversation_detail_read``       60  — the single-conversation endpoint
* ``dev:conversation_transcript_read``   25  — any read carrying a transcript

The pipeline lists without transcripts (cheap) and fetches transcripts only for
conversations it has not delivered yet, which is what keeps a routine run well
inside the 25/hour transcript budget.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .config import Config

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 8.0)
RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class OmiApiError(RuntimeError):
    """A Developer API call failed in a way this pipeline cannot recover from."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(OmiApiError):
    """The per-key hourly budget is exhausted."""

    def __init__(self, message: str, retry_after_seconds: Optional[float] = None):
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


class OmiClient:
    """Synchronous, read-only Developer API client."""

    def __init__(self, config: Config, transport: Optional[httpx.BaseTransport] = None, sleep=time.sleep):
        self._config = config
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=config.api_base,
            timeout=config.request_timeout_seconds,
            transport=transport,
            headers={
                "Authorization": "Bearer {}".format(config.api_key),
                "Accept": "application/json",
                "User-Agent": "omi-meeting-digest/0.1",
            },
        )

    def __enter__(self) -> "OmiClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def list_conversations(
        self,
        limit: int = 25,
        offset: int = 0,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        categories: Optional[str] = None,
        include_transcript: bool = False,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "include_transcript": include_transcript,
        }
        if start_date is not None:
            params["start_date"] = start_date.isoformat()
        if end_date is not None:
            params["end_date"] = end_date.isoformat()
        if categories:
            params["categories"] = categories

        payload = self._get("/v1/dev/user/conversations", params)
        if not isinstance(payload, list):
            raise OmiApiError("Expected a list of conversations, got {}".format(type(payload).__name__))
        return [item for item in payload if isinstance(item, dict)]

    def get_conversation(self, conversation_id: str, include_transcript: bool = True) -> Dict[str, Any]:
        if not conversation_id:
            raise ValueError("conversation_id must not be empty")
        payload = self._get(
            "/v1/dev/user/conversations/{}".format(conversation_id),
            {"include_transcript": include_transcript},
        )
        if not isinstance(payload, dict):
            raise OmiApiError("Expected a conversation object, got {}".format(type(payload).__name__))
        return payload

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = OmiApiError("{} failed: {}".format(path, type(exc).__name__))
                logger.warning(
                    "GET %s: transport error %s (attempt %d/%d)", path, type(exc).__name__, attempt, MAX_ATTEMPTS
                )
            else:
                if response.status_code == 200:
                    return response.json()

                detail = _error_detail(response)
                if response.status_code == 401:
                    raise OmiApiError("Unauthorized — the API key is invalid or revoked.", 401)
                if response.status_code == 403:
                    raise OmiApiError(
                        "Forbidden — the key lacks the required scope (conversations:read). {}".format(detail),
                        403,
                    )
                if response.status_code not in RETRYABLE_STATUS:
                    raise OmiApiError(
                        "GET {} failed: HTTP {} {}".format(path, response.status_code, detail), response.status_code
                    )

                last_error = _retryable_error(path, response, detail)
                logger.warning("GET %s: HTTP %d (attempt %d/%d)", path, response.status_code, attempt, MAX_ATTEMPTS)

            if attempt < MAX_ATTEMPTS:
                self._sleep(_backoff_for(attempt, last_error))

        assert last_error is not None
        raise last_error


def _backoff_for(attempt: int, error: Optional[Exception]) -> float:
    retry_after = getattr(error, "retry_after_seconds", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    index = min(attempt - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[index]


def _retryable_error(path: str, response: httpx.Response, detail: str) -> OmiApiError:
    if response.status_code == 429:
        return RateLimitedError(
            "Rate limited on {} — the hourly budget for this key is spent. {}".format(path, detail),
            retry_after_seconds=_retry_after(response),
        )
    return OmiApiError("GET {} failed: HTTP {} {}".format(path, response.status_code, detail), response.status_code)


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _error_detail(response: httpx.Response) -> str:
    """Extract a short server message without echoing a whole response body."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail[:200]
    return ""
