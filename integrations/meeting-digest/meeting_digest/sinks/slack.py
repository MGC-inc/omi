"""Post a short digest to a Slack Incoming Webhook.

Deliberately summary-only: the transcript is never sent. A sales call's verbatim
transcript in a Slack channel is readable by everyone in that channel and is
retained by Slack; the summary and action items are what a team actually needs
there. Whoever wants the full record opens the Markdown note.
"""

from typing import List, Optional

import httpx

from ..models import MeetingRecord, format_local
from .base import Sink, SinkError

MAX_ACTION_ITEMS = 10
MAX_OVERVIEW_CHARS = 1200


class SlackSink(Sink):
    name = "slack"
    supports_daily = True

    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: float = 15.0,
        client: Optional[httpx.Client] = None,
        utc_offset_hours: int = 9,
    ):
        if not webhook_url:
            raise ValueError("SlackSink requires a webhook URL")
        self._webhook_url = webhook_url
        self._utc_offset_hours = utc_offset_hours
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def deliver(self, record: MeetingRecord) -> None:
        self._post(render_slack_text(record, self._utc_offset_hours), record.id)

    def deliver_daily(self, summary) -> None:
        self._post(render_daily_slack_text(summary), "the {} review".format(summary.label))

    def _post(self, text: str, what: str) -> None:
        try:
            response = self._client.post(self._webhook_url, json={"text": text})
        except httpx.HTTPError as exc:
            raise SinkError("slack: request failed for {}: {}".format(what, type(exc).__name__))
        if response.status_code != 200:
            raise SinkError("slack: webhook returned HTTP {} for {}".format(response.status_code, what))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def render_slack_text(record: MeetingRecord, utc_offset_hours: int = 9) -> str:
    lines: List[str] = []
    header = "{} *{}*".format(record.emoji, record.title).strip()
    lines.append(header)

    meta = []
    if record.occurred_at:
        meta.append("{} UTC{:+d}".format(format_local(record.occurred_at, utc_offset_hours), utc_offset_hours))
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
                suffix += "（期限: {}）".format(format_local(item.due_at, utc_offset_hours, fmt="%Y-%m-%d"))
            lines.append("• {}{}".format(item.description or "(内容なし)", suffix))
        if len(open_items) > MAX_ACTION_ITEMS:
            lines.append("• ほか{}件".format(len(open_items) - MAX_ACTION_ITEMS))

    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


MAX_DAILY_LINES = 12


def render_daily_slack_text(summary) -> str:
    """The day's review, short enough to read without opening a thread."""
    offset = summary.utc_offset_hours
    lines: List[str] = ["🗓️ *{} の振り返り*".format(summary.label)]

    if summary.is_empty:
        lines.append("この日に記録された会話はありません。")
        return "\n".join(lines)

    lines.append("会話 {}件 ・ 合計 {}分".format(summary.conversation_count, summary.total_minutes))

    open_actions = summary.open_actions
    if open_actions:
        lines.append("")
        lines.append("*未完了のアクション（{}件）*".format(len(open_actions)))
        for record, item in open_actions[:MAX_DAILY_LINES]:
            suffix = ""
            if item.owner_name:
                suffix += "（担当: {}）".format(item.owner_name)
            if item.due_at:
                suffix += "（期限: {}）".format(format_local(item.due_at, offset, fmt="%Y-%m-%d"))
            lines.append("• {}{} — {}".format(item.description or "(内容なし)", suffix, record.title))
        if len(open_actions) > MAX_DAILY_LINES:
            lines.append("• ほか{}件".format(len(open_actions) - MAX_DAILY_LINES))

    lines.append("")
    lines.append("*この日の会話*")
    for record in summary.conversations[:MAX_DAILY_LINES]:
        stamp = format_local(record.occurred_at, offset, fmt="%H:%M", empty="--:--")
        lines.append("• {} {} {}".format(stamp, record.emoji, record.title))
    if summary.conversation_count > MAX_DAILY_LINES:
        lines.append("• ほか{}件".format(summary.conversation_count - MAX_DAILY_LINES))

    return "\n".join(lines)
