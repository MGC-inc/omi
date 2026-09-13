"""A local SQLite mirror of the conversations this pipeline has ingested.

It exists so other tools can read Omi data without holding an Omi key and
without spending the Developer API's hourly budget: the pipeline pays for a
conversation once, and every internal consumer reads it from here through the
HTTP API in ``server.py``.

Read paths open their own short-lived connection rather than sharing one, which
keeps the store safe to read from a multi-threaded server while the ingest
process writes to it.
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import MeetingRecord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    overview         TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT '',
    emoji            TEXT NOT NULL DEFAULT '',
    started_at       TEXT,
    finished_at      TEXT,
    created_at       TEXT,
    duration_minutes INTEGER,
    language         TEXT,
    source           TEXT,
    payload          TEXT NOT NULL,
    ingested_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_started_at ON conversations (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_category ON conversations (category);

CREATE TABLE IF NOT EXISTS action_items (
    conversation_id TEXT NOT NULL,
    position        INTEGER NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    completed       INTEGER NOT NULL DEFAULT 0,
    due_at          TEXT,
    owner_name      TEXT,
    context         TEXT,
    PRIMARY KEY (conversation_id, position),
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_action_items_completed ON action_items (completed, due_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    """The store cannot be opened or written."""


class ConversationStore:
    def __init__(self, path: str):
        self.path = path

    # --- lifecycle ----------------------------------------------------------

    def initialize(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=30.0)
        except sqlite3.Error as exc:
            raise StoreError("could not open the store at {}: {}".format(self.path, exc))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL lets the API read while an ingest run writes.
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    # --- writes -------------------------------------------------------------

    def upsert(self, record: MeetingRecord) -> None:
        """Replace the stored copy of one conversation. Safe to repeat."""
        payload = record.to_dict(include_transcript=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO conversations
                        (id, title, overview, category, emoji, started_at, finished_at, created_at,
                         duration_minutes, language, source, payload, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        title=excluded.title, overview=excluded.overview, category=excluded.category,
                        emoji=excluded.emoji, started_at=excluded.started_at, finished_at=excluded.finished_at,
                        created_at=excluded.created_at, duration_minutes=excluded.duration_minutes,
                        language=excluded.language, source=excluded.source, payload=excluded.payload,
                        ingested_at=excluded.ingested_at
                    """,
                    (
                        record.id,
                        record.title,
                        record.overview,
                        record.category,
                        record.emoji,
                        payload["started_at"],
                        payload["finished_at"],
                        payload["created_at"],
                        record.duration_minutes,
                        record.language,
                        record.source,
                        json.dumps(payload, ensure_ascii=False),
                        datetime.now().astimezone().isoformat(),
                    ),
                )
                connection.execute("DELETE FROM action_items WHERE conversation_id = ?", (record.id,))
                connection.executemany(
                    """
                    INSERT INTO action_items
                        (conversation_id, position, description, completed, due_at, owner_name, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            record.id,
                            index,
                            item.description,
                            1 if item.completed else 0,
                            item.due_at.isoformat() if item.due_at else None,
                            item.owner_name,
                            item.context,
                        )
                        for index, item in enumerate(record.action_items)
                    ],
                )
        except sqlite3.Error as exc:
            raise StoreError("could not store {}: {}".format(record.id, exc))

    # --- reads --------------------------------------------------------------

    def get(self, conversation_id: str, include_transcript: bool = True) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if not include_transcript:
            payload.pop("transcript", None)
        return payload

    def list(
        self,
        limit: int = 25,
        offset: int = 0,
        since: Optional[str] = None,
        until: Optional[str] = None,
        category: Optional[str] = None,
        query: Optional[str] = None,
        include_transcript: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        if until:
            clauses.append("started_at < ?")
            params.append(until)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            clauses.append("(title LIKE ? OR overview LIKE ? OR payload LIKE ?)")
            wildcard = "%{}%".format(query)
            params.extend([wildcard, wildcard, wildcard])

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM conversations{} ORDER BY started_at DESC LIMIT ? OFFSET ?".format(where),
                params,
            ).fetchall()

        results = []
        for row in rows:
            payload = json.loads(row["payload"])
            if not include_transcript:
                payload.pop("transcript", None)
            results.append(payload)
        return results

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]

    def action_items(
        self,
        open_only: bool = True,
        limit: int = 100,
        offset: int = 0,
        due_before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if open_only:
            clauses.append("a.completed = 0")
        if due_before:
            clauses.append("a.due_at IS NOT NULL AND a.due_at < ?")
            params.append(due_before)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.description, a.completed, a.due_at, a.owner_name, a.context,
                       a.conversation_id, c.title AS conversation_title, c.started_at
                FROM action_items a
                JOIN conversations c ON c.id = a.conversation_id
                {}
                ORDER BY (a.due_at IS NULL), a.due_at, c.started_at DESC
                LIMIT ? OFFSET ?
                """.format(where),
                params,
            ).fetchall()

        return [
            {
                "description": row["description"],
                "completed": bool(row["completed"]),
                "due_at": row["due_at"],
                "owner_name": row["owner_name"],
                "context": row["context"],
                "conversation": {
                    "id": row["conversation_id"],
                    "title": row["conversation_title"],
                    "started_at": row["started_at"],
                },
            }
            for row in rows
        ]
