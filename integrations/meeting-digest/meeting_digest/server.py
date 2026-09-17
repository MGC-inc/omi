"""Read-only HTTP API over the local store, for other internal tools.

This is the only inbound surface in the project, so it is fail-closed by
construction:

* a bearer token is **required** — the app refuses to start without one, so
  there is no window where the data is readable unauthenticated;
* the token is compared with ``hmac.compare_digest``;
* the default bind address is 127.0.0.1, so exposing it on a network is a
  deliberate act, not an accident;
* every verb is a read. Nothing here can mutate Omi data or the store.

It serves the pipeline's local mirror, never the Omi API, so a consumer needs no
Omi key and its requests cost nothing against the Developer API's hourly budget.
"""

import hmac
import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .config import Config
from .daily import build_daily_summary, local_day_bounds
from .models import MeetingRecord
from .store import ConversationStore, StoreError

logger = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 24


class ServerConfigError(RuntimeError):
    """The API cannot be started safely with this configuration."""


def create_app(config: Config, store: Optional[ConversationStore] = None) -> FastAPI:
    token = (config.api_server_token or "").strip()
    if not token:
        raise ServerConfigError(
            "MD_API_TOKEN is not set. The API serves conversation content, so it refuses to "
            "start without a bearer token. Generate one with: python3 -c "
            "'import secrets; print(secrets.token_urlsafe(32))'"
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise ServerConfigError(
            "MD_API_TOKEN is shorter than {} characters. Use a generated random token.".format(MIN_TOKEN_LENGTH)
        )

    conversations = store or ConversationStore(config.store_path)
    conversations.initialize()

    app = FastAPI(
        title="Omi meeting-digest API",
        version=__version__,
        description="Read-only access to locally ingested Omi conversations.",
    )
    bearer = HTTPBearer(auto_error=False)

    def require_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> None:
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")

    @app.get("/health", summary="Liveness probe (no authentication).")
    def health() -> Dict[str, Any]:
        # Deliberately carries no conversation data — it is the one open route.
        return {"status": "ok", "version": __version__}

    @app.get("/v1/conversations", summary="List ingested conversations, newest first.")
    def list_conversations(
        _: None = Depends(require_token),
        limit: int = Query(25, ge=1, le=200),
        offset: int = Query(0, ge=0),
        since: Optional[str] = Query(None, description="ISO timestamp lower bound on started_at."),
        until: Optional[str] = Query(None, description="ISO timestamp upper bound on started_at."),
        category: Optional[str] = None,
        q: Optional[str] = Query(None, description="Substring match over title, overview and body."),
        include_transcript: bool = False,
    ) -> Dict[str, Any]:
        items = _guard(
            lambda: conversations.list(
                limit=limit,
                offset=offset,
                since=since,
                until=until,
                category=category,
                query=q,
                include_transcript=include_transcript,
            )
        )
        return {"count": len(items), "limit": limit, "offset": offset, "items": items}

    @app.get("/v1/conversations/{conversation_id}", summary="One conversation, transcript included.")
    def get_conversation(
        conversation_id: str,
        _: None = Depends(require_token),
        include_transcript: bool = True,
    ) -> Dict[str, Any]:
        payload = _guard(lambda: conversations.get(conversation_id, include_transcript=include_transcript))
        if payload is None:
            raise HTTPException(status_code=404, detail="No ingested conversation with that id.")
        return payload

    @app.get("/v1/action-items", summary="Action items across conversations, soonest due first.")
    def list_action_items(
        _: None = Depends(require_token),
        open_only: bool = True,
        due_before: Optional[str] = Query(None, description="ISO timestamp; only items due before it."),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> Dict[str, Any]:
        items = _guard(
            lambda: conversations.action_items(open_only=open_only, limit=limit, offset=offset, due_before=due_before)
        )
        return {"count": len(items), "items": items}

    @app.get("/v1/daily/{day}", summary="The day's roll-up, built from the local mirror.")
    def daily(day: str, _: None = Depends(require_token)) -> Dict[str, Any]:
        try:
            target = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=400, detail="day must be an ISO date (YYYY-MM-DD).")

        start_utc, end_utc = local_day_bounds(target, config.utc_offset_hours)
        rows = _guard(
            lambda: conversations.list(
                limit=200,
                since=start_utc.isoformat(),
                until=end_utc.isoformat(),
                include_transcript=False,
            )
        )
        summary = build_daily_summary([MeetingRecord.from_stored(row) for row in rows], target, config.utc_offset_hours)
        return {
            "day": summary.label,
            "utc_offset_hours": summary.utc_offset_hours,
            "conversation_count": summary.conversation_count,
            "total_minutes": summary.total_minutes,
            "categories": [{"category": name, "count": count} for name, count in summary.category_counts],
            "open_actions": [
                {
                    "description": item.description,
                    "owner_name": item.owner_name,
                    "due_at": item.due_at.isoformat() if item.due_at else None,
                    "context": item.context,
                    "conversation": {"id": record.id, "title": record.title},
                }
                for record, item in summary.open_actions
            ],
            "conversations": [
                {
                    "id": record.id,
                    "title": record.title,
                    "overview": record.overview,
                    "started_at": record.started_at.isoformat() if record.started_at else None,
                    "duration_minutes": record.duration_minutes,
                    "category": record.category,
                }
                for record in summary.conversations
            ],
        }

    @app.get("/v1/stats", summary="How much the local mirror holds.")
    def stats(_: None = Depends(require_token)) -> Dict[str, Any]:
        return {"conversations": _guard(conversations.count), "store_path": config.store_path}

    return app


def _guard(operation):
    """Turn a store failure into a 503 rather than a stack trace."""
    try:
        return operation()
    except StoreError as exc:
        logger.error("store error: %s", exc)
        raise HTTPException(status_code=503, detail="The local store is unavailable.")


def serve(config: Config) -> int:
    import uvicorn

    app = create_app(config)
    if config.api_server_host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "binding to %s exposes conversation content beyond this machine; "
            "ensure the network path is trusted and the token is secret",
            config.api_server_host,
        )
    uvicorn.run(app, host=config.api_server_host, port=config.api_server_port, log_level="info")
    return 0
