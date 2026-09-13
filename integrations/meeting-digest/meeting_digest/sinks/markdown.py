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

from ..models import MeetingRecord, Utterance
from .base import Sink, SinkError

_UNSAFE = re.compile(r"[^\w\-]+", re.UNICODE)


class MarkdownSink(Sink):
    name = "markdown"

    def __init__(self, output_dir: str, include_transcript: bool = True):
        self._output_dir = output_dir
        self._include_transcript = include_transcript

    def deliver(self, record: MeetingRecord) -> None:
        try:
            os.makedirs(self._output_dir, exist_ok=True)
            path = os.path.join(self._output_dir, filename_for(record))
            body = render_markdown(record, include_transcript=self._include_transcript)
            _atomic_write(path, body)
        except OSError as exc:
            raise SinkError("markdown: could not write {}: {}".format(record.id, exc))


def filename_for(record: MeetingRecord) -> str:
    occurred = record.occurred_at
    stamp = occurred.strftime("%Y-%m-%d_%H%M") if occurred else "undated"
    slug = _slugify(record.title)
    short_id = (record.id or "unknown")[:8]
    return "{}_{}_{}.md".format(stamp, slug, short_id) if slug else "{}_{}.md".format(stamp, short_id)


def render_markdown(record: MeetingRecord, include_transcript: bool = True) -> str:
    lines: List[str] = []
    lines.append("# {} {}".format(record.emoji, record.title).strip())
    lines.append("")
    lines.extend(_metadata_block(record))
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
            start = event.start.strftime("%Y-%m-%d %H:%M UTC") if event.start else "日時未定"
            lines.append("- {} — {}（{}分）".format(event.title, start, event.duration_minutes))
        lines.append("")

    if include_transcript and record.transcript:
        lines.append("## 文字起こし")
        lines.append("")
        for utterance in record.transcript:
            lines.append(_transcript_line(utterance))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _metadata_block(record: MeetingRecord) -> List[str]:
    rows = [
        ("ID", record.id or "-"),
        ("開始", _format_time(record.started_at)),
        ("終了", _format_time(record.finished_at)),
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


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


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
