"""Post a short digest to a Slack Incoming Webhook.

Deliberately summary-only: the transcript is never sent. A sales call's verbatim
transcript in a Slack channel is readable by everyone in that channel and is
retained by Slack; the summary and action items are what a team actually needs
there. Whoever wants the full record opens the Markdown note.
"""

from typing import List, Optional

import httpx

from ..models import MeetingRecord
from .base import Sink, SinkError

MAX_ACTION_ITEMS = 10
MAX_OVERVIEW_CHARS = 1200


class SlackSink(Sink):
    name = "slack"

    def __init__(self, webhook_url: str, timeout_seconds: float = 15.0, client: Optional[httpx.Client] = None):
        if not webhook_url:
            raise ValueError("SlackSink requires a webhook URL")
        self._webhook_url = webhook_url
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def deliver(self, record: MeetingRecord) -> None:
        try:
            response = self._client.post(self._webhook_url, json={"text": render_slack_text(record)})
        except httpx.HTTPError as exc:
            raise SinkError("slack: request failed for {}: {}".format(record.id, type(exc).__name__))

        if response.status_code != 200:
            raise SinkError("slack: webhook returned HTTP {} for {}".format(response.status_code, record.id))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def render_slack_text(record: MeetingRecord) -> str:
    lines: List[str] = []
    header = "{} *{}*".format(record.emoji, record.title).strip()
    lines.append(header)

    when = record.occurred_at
    meta = []
    if when:
        meta.append(when.strftime("%Y-%m-%d %H:%M UTC"))
    if record.duration_minutes:
        meta.append("{}分".format(record.duration_minutes))
    if record.category:
        meta.append(record.category)
    if meta:
        lines.append(" · ".join(meta))

    if record.overview:
        lines.append("")
        lines.append(_truncate(record.overview, MAX_OVERVIEW_CHARS))

    open_items = record.open_action_items
    if open_items:
        lines.append("")
        lines.append("*未完了のアクション*")
        for item in open_items[:MAX_ACTION_ITEMS]:
            suffix = ""
            if item.owner_name:
                suffix += "（担当: {}）".format(item.owner_name)
            if item.due_at:
                suffix += "（期限: {}）".format(item.due_at.strftime("%Y-%m-%d"))
            lines.append("• {}{}".format(item.description or "(内容なし)", suffix))
        if len(open_items) > MAX_ACTION_ITEMS:
            lines.append("• ほか{}件".format(len(open_items) - MAX_ACTION_ITEMS))

    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
