"""Runtime configuration, resolved from environment variables.

The API key is read from the environment and never written to disk, logs, or
delivered payloads. ``Config.redacted()`` is what any diagnostic output uses.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_API_BASE = "https://api.omi.me"

# The Developer API caps transcript-bearing reads at 25 per hour per key
# (backend/utils/rate_limit_config.py: "dev:conversation_transcript_read"), and
# every conversation read also consumes the 60/hour total budget
# ("dev:conversation_reads_total"). One run therefore fetches at most this many
# transcripts; the rest are picked up by the next run.
DEFAULT_MAX_TRANSCRIPT_FETCHES = 20


class ConfigError(RuntimeError):
    """Raised when the environment does not carry a usable configuration."""


@dataclass(frozen=True)
class Config:
    api_key: str
    api_base: str = DEFAULT_API_BASE
    lookback_hours: int = 24
    list_page_size: int = 50
    max_transcript_fetches: int = DEFAULT_MAX_TRANSCRIPT_FETCHES
    categories: Optional[str] = None
    state_path: str = "state/processed.json"
    output_dir: str = "out"
    store_path: str = "state/conversations.db"
    slack_webhook_url: Optional[str] = None
    notion_token: Optional[str] = None
    notion_database_id: Optional[str] = None
    include_transcript_in_markdown: bool = True
    include_transcript_in_notion: bool = True
    utc_offset_hours: int = 9
    api_server_token: Optional[str] = None
    api_server_host: str = "127.0.0.1"
    api_server_port: int = 8787
    request_timeout_seconds: float = 30.0
    sinks: List[str] = field(default_factory=lambda: ["markdown"])

    @staticmethod
    def from_env(environ: Optional[dict] = None) -> "Config":
        env = os.environ if environ is None else environ

        api_key = (env.get("OMI_API_KEY") or "").strip()
        if not api_key:
            raise ConfigError(
                "OMI_API_KEY is not set. Create a read-only Developer API key "
                "(conversations:read) at app.omi.me and export it."
            )
        if not api_key.startswith("omi_dev_"):
            raise ConfigError(
                "OMI_API_KEY does not look like a Developer API key (expected an 'omi_dev_' prefix). "
                "MCP keys (omi_mcp_) do not authenticate the Developer API."
            )

        sinks = [s.strip() for s in (env.get("MD_SINKS") or "markdown").split(",") if s.strip()]
        if not sinks:
            raise ConfigError("MD_SINKS resolved to an empty list; set at least one sink.")

        return Config(
            api_key=api_key,
            api_base=(env.get("OMI_API_BASE") or DEFAULT_API_BASE).rstrip("/"),
            lookback_hours=_positive_int(env, "MD_LOOKBACK_HOURS", 24),
            list_page_size=_bounded_int(env, "MD_LIST_PAGE_SIZE", 50, 1, 100),
            max_transcript_fetches=_positive_int(env, "MD_MAX_TRANSCRIPT_FETCHES", DEFAULT_MAX_TRANSCRIPT_FETCHES),
            categories=(env.get("MD_CATEGORIES") or "").strip() or None,
            state_path=env.get("MD_STATE_PATH") or "state/processed.json",
            output_dir=env.get("MD_OUTPUT_DIR") or "out",
            store_path=env.get("MD_STORE_PATH") or "state/conversations.db",
            slack_webhook_url=(env.get("MD_SLACK_WEBHOOK_URL") or "").strip() or None,
            notion_token=(env.get("MD_NOTION_TOKEN") or "").strip() or None,
            notion_database_id=_normalize_notion_id(env.get("MD_NOTION_DATABASE_ID")),
            include_transcript_in_markdown=_flag(env, "MD_MARKDOWN_INCLUDE_TRANSCRIPT", True),
            include_transcript_in_notion=_flag(env, "MD_NOTION_INCLUDE_TRANSCRIPT", True),
            utc_offset_hours=_offset_int(env, "MD_UTC_OFFSET_HOURS", 9),
            api_server_token=(env.get("MD_API_TOKEN") or "").strip() or None,
            api_server_host=(env.get("MD_API_HOST") or "127.0.0.1").strip(),
            api_server_port=_bounded_int(env, "MD_API_PORT", 8787, 1, 65535),
            request_timeout_seconds=float(env.get("MD_REQUEST_TIMEOUT_SECONDS") or 30.0),
            sinks=sinks,
        )

    def redacted(self) -> dict:
        """A dict safe to log: the key is reduced to its non-secret prefix."""
        return {
            "api_base": self.api_base,
            "api_key": _redact_key(self.api_key),
            "lookback_hours": self.lookback_hours,
            "max_transcript_fetches": self.max_transcript_fetches,
            "categories": self.categories,
            "state_path": self.state_path,
            "output_dir": self.output_dir,
            "store_path": self.store_path,
            "slack_webhook_configured": self.slack_webhook_url is not None,
            "notion_token_configured": self.notion_token is not None,
            "notion_database_id": self.notion_database_id,
            "utc_offset_hours": self.utc_offset_hours,
            "api_token_configured": self.api_server_token is not None,
            "api_bind": "{}:{}".format(self.api_server_host, self.api_server_port),
            "sinks": list(self.sinks),
        }


def _normalize_notion_id(value) -> Optional[str]:
    """Accept a raw 32-hex id, a dashed UUID, or a pasted Notion database URL."""
    text = (value or "").strip()
    if not text:
        return None
    if "notion.so" in text or text.startswith("http"):
        # .../<workspace>/<database-id>?v=<view-id> — the path segment is the id.
        tail = text.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        text = tail.rsplit("-", 1)[-1] if len(tail.rsplit("-", 1)[-1]) == 32 else tail
    compact = text.replace("-", "")
    if len(compact) != 32 or not all(c in "0123456789abcdefABCDEF" for c in compact):
        raise ConfigError(
            "MD_NOTION_DATABASE_ID does not look like a Notion database id "
            "(expected 32 hex characters, with or without dashes). Got {!r}".format(value)
        )
    return compact


def _redact_key(api_key: str) -> str:
    prefix = "omi_dev_"
    if not api_key.startswith(prefix) or len(api_key) <= len(prefix) + 4:
        return "***"
    return prefix + api_key[len(prefix) : len(prefix) + 4] + "..."


def _offset_int(env, name: str, default: int) -> int:
    """A UTC offset in whole hours, used to decide which local day a conversation belongs to."""
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("{} must be an integer number of hours, got {!r}".format(name, raw))
    if not -12 <= value <= 14:
        raise ConfigError("{} must be between -12 and 14, got {}".format(name, value))
    return value


def _positive_int(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("{} must be an integer, got {!r}".format(name, raw))
    if value < 1:
        raise ConfigError("{} must be >= 1, got {}".format(name, value))
    return value


def _bounded_int(env, name: str, default: int, low: int, high: int) -> int:
    value = _positive_int(env, name, default)
    if not low <= value <= high:
        raise ConfigError("{} must be between {} and {}, got {}".format(name, low, high, value))
    return value


def _flag(env, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
