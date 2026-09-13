"""Sink registry.

Adding a destination (Notion, a internal database, email) means adding a module
here and one entry in ``build_sinks``. Nothing else in the pipeline changes.
"""

from typing import List

from ..config import Config, ConfigError
from .base import Sink, SinkError
from .markdown import MarkdownSink
from .slack import SlackSink

__all__ = ["Sink", "SinkError", "MarkdownSink", "SlackSink", "build_sinks"]


def build_sinks(config: Config) -> List[Sink]:
    sinks: List[Sink] = []
    for name in config.sinks:
        if name == "markdown":
            sinks.append(MarkdownSink(config.output_dir, include_transcript=config.include_transcript_in_markdown))
        elif name == "slack":
            if not config.slack_webhook_url:
                raise ConfigError("Sink 'slack' is enabled but MD_SLACK_WEBHOOK_URL is not set.")
            sinks.append(SlackSink(config.slack_webhook_url, timeout_seconds=config.request_timeout_seconds))
        else:
            raise ConfigError("Unknown sink {!r}. Known sinks: markdown, slack.".format(name))
    return sinks
