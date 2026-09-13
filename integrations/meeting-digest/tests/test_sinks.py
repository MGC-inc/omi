import os

from meeting_digest.models import MeetingRecord
from meeting_digest.sinks.markdown import MarkdownSink, filename_for, render_markdown
from meeting_digest.sinks.slack import render_daily_slack_text, render_slack_text
from tests.fixtures import conversation_payload


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


def test_slack_digest_never_contains_transcript_text():
    # The transcript stays out of Slack by design: a channel is a wider audience
    # and a longer retention than a sales-call transcript should get.
    record = _record()
    text = render_slack_text(record)

    for utterance in record.transcript:
        assert utterance.text not in text


def test_slack_digest_carries_summary_and_open_actions():
    text = render_slack_text(_record())

    assert "A社との商談" in text
    assert "見積書を送付する" in text
    assert "松尾" in text
    # Completed items are not chased in the channel.
    assert "議事録を共有する" not in text


def test_markdown_metadata_shows_local_time_with_its_offset():
    body = render_markdown(_record(), utc_offset_hours=9)

    assert "| 開始 | 2026-09-10 10:00 UTC+9 |" in body


def test_markdown_note_contains_transcript_when_enabled():
    body = render_markdown(_record(), include_transcript=True)

    assert "## 文字起こし" in body
    assert "本日はお時間をいただきありがとうございます。" in body


def test_markdown_note_omits_transcript_when_disabled():
    body = render_markdown(_record(), include_transcript=False)

    assert "## 文字起こし" not in body
    assert "本日はお時間をいただきありがとうございます。" not in body
    # The summary survives either way.
    assert "新規導入の初回商談" in body


def test_markdown_filename_is_sortable_and_collision_resistant():
    # Named in local time (+9 here): 01:00 UTC is 10:00 JST, and the file should
    # sort where the reader remembers the meeting happening.
    name = filename_for(_record(), utc_offset_hours=9)

    assert name.startswith("2026-09-10_1000_")
    assert name.endswith(".md")
    assert "conv_001"[:8] in name


def test_markdown_filename_follows_the_configured_offset():
    assert filename_for(_record(), utc_offset_hours=0).startswith("2026-09-10_0100_")


def test_markdown_sink_writes_a_file(tmp_path):
    sink = MarkdownSink(str(tmp_path))
    sink.deliver(_record())

    written = os.listdir(str(tmp_path))
    assert len(written) == 1
    assert written[0].endswith(".md")


def test_markdown_sink_is_safe_to_call_twice(tmp_path):
    # A run that crashes after delivering but before saving state retries it.
    sink = MarkdownSink(str(tmp_path))
    record = _record()
    sink.deliver(record)
    sink.deliver(record)

    assert len(os.listdir(str(tmp_path))) == 1


def test_slack_digest_shows_local_time_not_utc():
    # 01:00 UTC is 10:00 JST; a digest that says 01:00 reads as the wrong meeting.
    text = render_slack_text(_record(), utc_offset_hours=9)

    assert "2026-09-10 10:00 UTC+9" in text


def test_daily_slack_digest_lists_conversations_in_local_time():
    from datetime import date

    from meeting_digest.daily import build_daily_summary

    summary = build_daily_summary([_record()], date(2026, 9, 10), 9)
    text = render_daily_slack_text(summary)

    assert "10:00" in text
    assert "01:00" not in text


def test_daily_markdown_lists_conversations_in_local_time():
    from datetime import date

    from meeting_digest.daily import build_daily_summary
    from meeting_digest.sinks.markdown import render_daily_markdown

    summary = build_daily_summary([_record()], date(2026, 9, 10), 9)
    body = render_daily_markdown(summary)

    assert "### 10:00" in body
