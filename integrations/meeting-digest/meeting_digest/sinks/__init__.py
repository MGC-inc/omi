"""Sink registry.

Adding a destination (Notion, a internal database, email) means adding a module
here and one entry in ``build_sinks``. Nothing else in the pipeline changes.
"""

from typing import List

from ..config import Config, ConfigError
from ..store import ConversationStore
from .base import Sink, SinkError
from .markdown import MarkdownSink
from .notion import NotionSink
from .slack import SlackSink
from .store import StoreSink

__all__ = ["Sink", "SinkError", "MarkdownSink", "NotionSink", "SlackSink", "StoreSink", "build_sinks"]


def build_sinks(config: Config) -> List[Sink]:
    sinks: List[Sink] = []
    for name in config.sinks:
        if name == "markdown":
            sinks.append(
                MarkdownSink(
                    config.output_dir,
                    include_transcript=config.include_transcript_in_markdown,
                    utc_offset_hours=config.utc_offset_hours,
                )
            )
        elif name == "slack":
            if not config.slack_webhook_url:
                raise ConfigError("Sink 'slack' is enabled but MD_SLACK_WEBHOOK_URL is not set.")
            sinks.append(
                SlackSink(
                    config.slack_webhook_url,
                    timeout_seconds=config.request_timeout_seconds,
                    utc_offset_hours=config.utc_offset_hours,
                )
            )
        elif name == "notion":
            if not config.notion_token:
                raise ConfigError("Sink 'notion' is enabled but MD_NOTION_TOKEN is not set.")
            if not config.notion_database_id:
                raise ConfigError("Sink 'notion' is enabled but MD_NOTION_DATABASE_ID is not set.")
            sinks.append(
                NotionSink(
                    config.notion_token,
                    config.notion_database_id,
                    include_transcript=config.include_transcript_in_notion,
                    timeout_seconds=config.request_timeout_seconds,
                    utc_offset_hours=config.utc_offset_hours,
                )
            )
        elif name == "store":
            sinks.append(StoreSink(ConversationStore(config.store_path)))
        else:
            raise ConfigError("Unknown sink {!r}. Known sinks: markdown, slack, notion, store.".format(name))
    return sinks
