"""Write each conversation to a Markdown file on local disk.

This is the default sink: it keeps the data inside whatever storage the operator
already controls, which is the right starting point for meeting and sales-call
records. Writes go to a temp file and are renamed into place, so a crashed run
never leaves a half-written note.
"""

import os
import re
import tempfile
from typing import List, Optional

from ..models import MeetingRecord, Utterance, format_local, to_local
from .base import Sink, SinkError

_UNSAFE = re.compile(r"[^\w\-]+", re.UNICODE)


class MarkdownSink(Sink):
    name = "markdown"
    supports_daily = True

    def __init__(self, output_dir: str, include_transcript: bool = True, utc_offset_hours: int = 9):
        self._output_dir = output_dir
        self._include_transcript = include_transcript
        self._utc_offset_hours = utc_offset_hours

    def deliver(self, record: MeetingRecord) -> None:
        try:
            os.makedirs(self._output_dir, exist_ok=True)
            path = os.path.join(self._output_dir, filename_for(record, self._utc_offset_hours))
            body = render_markdown(
                record,
                include_transcript=self._include_transcript,
                utc_offset_hours=self._utc_offset_hours,
            )
            _atomic_write(path, body)
        except OSError as exc:
            raise SinkError("markdown: could not write {}: {}".format(record.id, exc))

    def deliver_daily(self, summary) -> None:
        try:
            directory = os.path.join(self._output_dir, "daily")
            os.makedirs(directory, exist_ok=True)
            _atomic_write(os.path.join(directory, daily_filename_for(summary)), render_daily_markdown(summary))
        except OSError as exc:
            raise SinkError("markdown: could not write the {} review: {}".format(summary.label, exc))


def filename_for(record: MeetingRecord, utc_offset_hours: int = 9) -> str:
    """Named by local time, so files sort the way the day was actually lived."""
    occurred = to_local(record.occurred_at, utc_offset_hours)
    stamp = occurred.strftime("%Y-%m-%d_%H%M") if occurred else "undated"
    slug = _slugify(record.title)
    short_id = (record.id or "unknown")[:8]
    return "{}_{}_{}.md".format(stamp, slug, short_id) if slug else "{}_{}.md".format(stamp, short_id)


def render_markdown(record: MeetingRecord, include_transcript: bool = True, utc_offset_hours: int = 9) -> str:
    lines: List[str] = []
    lines.append("# {} {}".format(record.emoji, record.title).strip())
    lines.append("")
    lines.extend(_metadata_block(record, utc_offset_hours))
    lines.append("")

    if record.overview:
        lines.append("## 概要")
        lines.append("")
        lines.append(record.overview)
        lines.append("")

    for section in record.sections:
        if not section.heading and not section.body_markdown:
            continue
        lines.append("## {}".format(section.heading or "詳細"))
        lines.append("")
        lines.append(section.body_markdown)
        lines.append("")

    if record.action_items:
        lines.append("## アクションアイテム")
        lines.append("")
        for item in record.action_items:
            lines.append(_action_item_line(item))
        lines.append("")

    if record.events:
        lines.append("## 予定")
        lines.append("")
        for event in record.events:
            start = _format_time(event.start, utc_offset_hours, "UTC{:+d}".format(utc_offset_hours))
            lines.append(
                "- {} — {}（{}分）".format(event.title, start if event.start else "日時未定", event.duration_minutes)
            )
        lines.append("")

    if include_transcript and record.transcript:
        lines.append("## 文字起こし")
        lines.append("")
        for utterance in record.transcript:
            lines.append(_transcript_line(utterance))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _metadata_block(record: MeetingRecord, utc_offset_hours: int) -> List[str]:
    label = "UTC{:+d}".format(utc_offset_hours)
    rows = [
        ("ID", record.id or "-"),
        ("開始", _format_time(record.started_at, utc_offset_hours, label)),
        ("終了", _format_time(record.finished_at, utc_offset_hours, label)),
        ("長さ", "{}分".format(record.duration_minutes) if record.duration_minutes else "-"),
        ("カテゴリ", record.category or "-"),
        ("言語", record.language or "-"),
        ("ソース", record.source or "-"),
    ]
    lines = ["| 項目 | 値 |", "| --- | --- |"]
    lines.extend("| {} | {} |".format(label, value) for label, value in rows)
    return lines


def _action_item_line(item) -> str:
    box = "[x]" if item.completed else "[ ]"
    parts = ["- {} {}".format(box, item.description or "(内容なし)")]
    if item.owner_name:
        parts.append("（担当: {}）".format(item.owner_name))
    if item.due_at:
        parts.append("（期限: {}）".format(item.due_at.strftime("%Y-%m-%d")))
    if item.context:
        parts.append("— {}".format(item.context))
    return " ".join(parts)


def _transcript_line(utterance: Utterance) -> str:
    speaker = "自分" if utterance.is_user else utterance.speaker
    return "- **{}** [{}] {}".format(speaker, _format_offset(utterance.start_seconds), utterance.text)


def _format_offset(seconds: float) -> str:
    total = max(0, int(seconds))
    return "{:02d}:{:02d}".format(total // 60, total % 60)


def _format_time(value, utc_offset_hours: int, label: str) -> str:
    if value is None:
        return "-"
    return "{} {}".format(format_local(value, utc_offset_hours), label)


def _slugify(title: str) -> str:
    slug = _UNSAFE.sub("-", (title or "").strip()).strip("-")
    return slug[:48]


def _atomic_write(path: str, body: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".note-", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(body)
        os.replace(handle.name, path)
    except BaseException:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
        raise


def daily_filename_for(summary) -> str:
    return "{}.md".format(summary.label)


def render_daily_markdown(summary) -> str:
    """The day's review as one note. Re-rendered wholesale on each re-run."""
    offset = summary.utc_offset_hours
    lines: List[str] = []
    lines.append("# 🗓️ {} の振り返り".format(summary.label))
    lines.append("")

    if summary.is_empty:
        lines.append("この日に記録された会話はありません。")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        "会話 {}件 ・ 合計 {}分（UTC{:+d} 基準）".format(
            summary.conversation_count, summary.total_minutes, summary.utc_offset_hours
        )
    )
    lines.append("")

    categories = summary.category_counts
    if categories:
        lines.append("カテゴリ: " + " ・ ".join("{} {}件".format(name, count) for name, count in categories))
        lines.append("")

    open_actions = summary.open_actions
    if open_actions:
        lines.append("## 未完了のアクション")
        lines.append("")
        for record, item in open_actions:
            lines.append("{} ← {}".format(_action_item_line(item), record.title))
        lines.append("")

    events = summary.upcoming_events
    if events:
        lines.append("## 予定")
        lines.append("")
        for record, event in events:
            start = "{} UTC{:+d}".format(format_local(event.start, offset), offset) if event.start else "日時未定"
            lines.append("- {} — {}（{}分） ← {}".format(event.title, start, event.duration_minutes, record.title))
        lines.append("")

    lines.append("## この日の会話")
    lines.append("")
    for record in summary.conversations:
        stamp = format_local(record.occurred_at, offset, fmt="%H:%M", empty="--:--")
        duration = "{}分".format(record.duration_minutes) if record.duration_minutes else "-"
        lines.append("### {} {} {}（{}）".format(stamp, record.emoji, record.title, duration))
        lines.append("")
        if record.overview:
            lines.append(record.overview)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
