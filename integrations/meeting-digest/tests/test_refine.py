import pytest

from meeting_digest.llm import LLMError
from meeting_digest.models import MeetingRecord
from meeting_digest.refine import (
    MAX_CHARS_PER_REQUEST,
    TranscriptRefiner,
    refine_record,
    render_raw_transcript,
    split_for_request,
)
from tests.fake_llm import FakeLLM, fatal, transient
from tests.fixtures import conversation_payload


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


# --- request shape ----------------------------------------------------------


def test_the_system_prompt_forbids_inventing_content():
    # The whole value of the cleaned copy rests on it being a subset of what
    # was said. If this instruction drifts, the output stops being a record.
    llm = FakeLLM()
    TranscriptRefiner(llm).refine(_record())

    system = llm.calls[0]["system"]
    assert "推測で内容を補わない" in system
    assert "要約しない" in system


def test_the_raw_transcript_is_what_gets_sent():
    llm = FakeLLM()
    TranscriptRefiner(llm).refine(_record())

    sent = llm.calls[0]["user"]
    assert "本日はお時間をいただきありがとうございます。" in sent
    assert "[00:00]" in sent


def test_a_conversation_without_a_transcript_makes_no_call():
    llm = FakeLLM()

    assert TranscriptRefiner(llm).refine(_record(with_transcript=False)) is None
    assert llm.calls == []


# --- chunking ---------------------------------------------------------------


def test_short_transcripts_are_one_request():
    assert len(split_for_request("a\nb\nc", MAX_CHARS_PER_REQUEST)) == 1


def test_long_transcripts_split_on_line_boundaries():
    lines = ["[00:0{}] 話者: {}".format(i % 10, "あ" * 100) for i in range(50)]
    text = "\n".join(lines)

    chunks = split_for_request(text, 1000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "\n".join(chunks) == text  # no utterance was cut in half


def test_each_chunk_becomes_its_own_request_and_the_parts_are_rejoined():
    # Four ~520-char lines at a 1200-char limit pack two per chunk.
    payload = conversation_payload()
    payload["transcript_segments"] = [
        {
            "id": str(i),
            "text": "あ" * 500,
            "speaker": "SPEAKER_00",
            "is_user": False,
            "start": float(i),
            "end": float(i) + 1,
        }
        for i in range(4)
    ]
    llm = FakeLLM(text_responses=["前半", "後半"])

    result = TranscriptRefiner(llm, max_chars_per_request=1200).refine(MeetingRecord.from_api(payload))

    assert len([c for c in llm.calls if c["kind"] == "text"]) == 2
    assert result == "前半\n\n後半"


# --- failure handling -------------------------------------------------------


def test_a_fatal_failure_propagates_out_of_refine_record():
    # So the caller can stop refining instead of repeating the same failed call
    # once per conversation.
    with pytest.raises(LLMError):
        refine_record(_record(), TranscriptRefiner(FakeLLM(error=fatal())))


def test_a_transient_failure_leaves_the_record_deliverable():
    # Losing the conversation because its cleanup failed is the worse outcome.
    record = _record()

    refined = refine_record(record, TranscriptRefiner(FakeLLM(error=transient())))

    assert refined.refined_transcript is None
    assert refined.has_transcript


# --- integration with the record -------------------------------------------


def test_refine_record_attaches_the_cleaned_copy_without_touching_the_raw_one():
    record = _record()

    refined = refine_record(record, TranscriptRefiner(FakeLLM(text_responses=["整えた本文"])))

    assert refined.refined_transcript == "整えた本文"
    assert [u.text for u in refined.transcript] == [u.text for u in record.transcript]


def test_the_cleaned_copy_survives_the_store_round_trip():
    record = _record().with_refined_transcript("整えた本文")

    assert MeetingRecord.from_stored(record.to_dict()).refined_transcript == "整えた本文"


def test_raw_rendering_marks_the_user_and_drops_empty_utterances():
    payload = conversation_payload()
    payload["transcript_segments"].append(
        {"id": "blank", "text": "   ", "speaker": "SPEAKER_02", "is_user": False, "start": 20.0, "end": 21.0}
    )

    rendered = render_raw_transcript(MeetingRecord.from_api(payload).transcript)

    assert "自分:" in rendered
    assert "SPEAKER_02" not in rendered
