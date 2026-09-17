"""Delivery state: which conversation reached which sink.

Tracked per (conversation, sink) rather than per conversation, so a run where
Slack failed and Markdown succeeded re-sends only the Slack half next time.

A corrupt state file is a hard error, never an implicit reset: silently starting
from an empty state would re-deliver every conversation in the lookback window
to every sink.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, Iterable, Set

STATE_VERSION = 1


class StateError(RuntimeError):
    """The state file exists but cannot be used."""


class DeliveryState:
    def __init__(self, path: str, delivered: Dict[str, Dict[str, str]]):
        self.path = path
        self._delivered = delivered

    @staticmethod
    def load(path: str) -> "DeliveryState":
        if not os.path.exists(path):
            return DeliveryState(path, {})
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StateError(
                "Could not read the delivery state at {}: {}. Fix or move the file — "
                "deleting it re-delivers every conversation in the lookback window.".format(path, exc)
            )

        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            raise StateError("Unrecognized delivery state at {} (expected version {}).".format(path, STATE_VERSION))

        delivered_raw = raw.get("delivered")
        if not isinstance(delivered_raw, dict):
            raise StateError("Delivery state at {} has no usable 'delivered' map.".format(path))

        delivered: Dict[str, Dict[str, str]] = {}
        for conversation_id, sinks in delivered_raw.items():
            if isinstance(conversation_id, str) and isinstance(sinks, dict):
                delivered[conversation_id] = {
                    name: value for name, value in sinks.items() if isinstance(name, str) and isinstance(value, str)
                }
        return DeliveryState(path, delivered)

    def is_delivered(self, conversation_id: str, sink_name: str) -> bool:
        return sink_name in self._delivered.get(conversation_id, {})

    def pending_sinks(self, conversation_id: str, sink_names: Iterable[str]) -> Set[str]:
        done = self._delivered.get(conversation_id, {})
        return {name for name in sink_names if name not in done}

    def mark_delivered(self, conversation_id: str, sink_name: str) -> None:
        self._delivered.setdefault(conversation_id, {})[sink_name] = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        """Write atomically so an interrupted run cannot truncate the state."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        payload = {"version": STATE_VERSION, "delivered": self._delivered}

        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".state-", suffix=".tmp", delete=False
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
            raise

    def __len__(self) -> int:
        return len(self._delivered)
