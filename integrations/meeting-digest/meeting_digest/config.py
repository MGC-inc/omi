"""Runtime configuration, resolved from environment variables.

The API key is read from the environment and never written to disk, logs, or
delivered payloads. ``Config.redacted()`` is what any diagnostic output uses.
"""

import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ENV_FILE = ".env"

DEFAULT_API_BASE = "https://api.omi.me"

# The Developer API caps transcript-bearing reads at 25 per hour per key
# (backend/utils/rate_limit_config.py: "dev:conversation_transcript_read"), and
# every conversation read also consumes the 60/hour total budget
# ("dev:conversation_reads_total"). One run therefore fetches at most this many
# transcripts; the rest are picked up by the next run.
DEFAULT_MAX_TRANSCRIPT_FETCHES = 20


class ConfigError(RuntimeError):
    """Raised when the environment does not carry a usable configuration."""


def load_env_file(path: Optional[str] = None, environ: Optional[Dict[str, str]] = None) -> int:
    """Load ``KEY=VALUE`` lines from a .env file into the environment.

    Returns the number of variables set. Existing environment variables always
    win, so a one-off ``OMI_API_KEY=... python -m meeting_digest`` still
    overrides the file, and a missing file is simply a no-op.

    The file holds an API key, so a mode readable by other users on the machine
    draws a warning — not an error, since refusing to run would be worse than
    running with a note in the log.
    """
    target = path or os.environ.get("MD_ENV_FILE") or DEFAULT_ENV_FILE
    env = os.environ if environ is None else environ

    if not os.path.isfile(target):
        return 0

    _warn_if_world_readable(target)

    applied = 0
    with open(target, "r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].lstrip()
            if "=" not in line:
                logger.warning("%s:%d: ignoring a line without '='", target, number)
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            if key in env:
                continue
            env[key] = _unquote(value.strip())
            applied += 1
    return applied


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _warn_if_world_readable(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        logger.warning(
            "%s is readable by other users on this machine and holds an API key. " "Restrict it with: chmod 600 %s",
            path,
            path,
        )


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
    notion_property_names: Dict[str, str] = field(default_factory=dict)
    include_transcript_in_markdown: bool = True
    include_transcript_in_notion: bool = False
    min_duration_minutes: int = 0
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
            notion_property_names=_notion_property_names(env),
            include_transcript_in_notion=_flag(env, "MD_NOTION_INCLUDE_TRANSCRIPT", False),
            min_duration_minutes=_non_negative_int(env, "MD_MIN_DURATION_MINUTES", 0),
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
            "notion_property_names": dict(self.notion_property_names),
            "min_duration_minutes": self.min_duration_minutes,
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


#: Canonical property key -> the environment variable that renames it. The
#: canonical keys are what the Notion sink asks for; the database decides what
#: they are actually called, which for a Japanese workspace is rarely English.
NOTION_PROPERTY_ENV = {
    "Date": "MD_NOTION_PROP_DATE",
    "Category": "MD_NOTION_PROP_CATEGORY",
    "Duration (min)": "MD_NOTION_PROP_DURATION",
    "Open Actions": "MD_NOTION_PROP_OPEN_ACTIONS",
    "Language": "MD_NOTION_PROP_LANGUAGE",
    "Omi ID": "MD_NOTION_PROP_OMI_ID",
}


def _notion_property_names(env) -> Dict[str, str]:
    """Resolve each canonical property to the column name in this database."""
    resolved = {}
    for canonical, variable in NOTION_PROPERTY_ENV.items():
        configured = (env.get(variable) or "").strip()
        resolved[canonical] = configured or canonical
    return resolved


def _non_negative_int(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("{} must be an integer, got {!r}".format(name, raw))
    if value < 0:
        raise ConfigError("{} must be >= 0, got {}".format(name, value))
    return value


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
