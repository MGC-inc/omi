import pytest

from meeting_digest.models import MeetingRecord
from meeting_digest.refine import (
    MAX_CHARS_PER_REQUEST,
    RefinementError,
    TranscriptRefiner,
    refine_record,
    render_raw_transcript,
    split_for_request,
)
from tests.fixtures import conversation_payload


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)] if text else []
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses=None, error=None):
        self._responses = list(responses or [])
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._responses.pop(0) if self._responses else FakeResponse("整形済みテキスト")


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.messages = FakeMessages(responses, error)


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


# --- request shape ----------------------------------------------------------


def test_uses_the_configured_model():
    client = FakeClient()
    TranscriptRefiner(client=client, model="claude-opus-5").refine(_record())

    assert client.messages.calls[0]["model"] == "claude-opus-5"


def test_the_system_prompt_forbids_inventing_content():
    # The whole value of the cleaned copy rests on it being a subset of what
    # was said. If this instruction drifts, the output stops being a record.
    client = FakeClient()
    TranscriptRefiner(client=client).refine(_record())

    system = client.messages.calls[0]["system"]
    assert "推測で内容を補わない" in system
    assert "要約しない" in system


def test_the_raw_transcript_is_what_gets_sent():
    client = FakeClient()
    TranscriptRefiner(client=client).refine(_record())

    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "本日はお時間をいただきありがとうございます。" in sent
    assert "[00:00]" in sent


def test_a_conversation_without_a_transcript_makes_no_call():
    client = FakeClient()
    result = TranscriptRefiner(client=client).refine(_record(with_transcript=False))

    assert result is None
    assert client.messages.calls == []


# --- chunking ---------------------------------------------------------------


def test_short_transcripts_are_one_request():
    assert len(split_for_request("a\nb\nc", MAX_CHARS_PER_REQUEST)) == 1


def test_long_transcripts_split_on_line_boundaries():
    lines = ["[00:0{}] 話者: {}".format(i % 10, "あ" * 100) for i in range(50)]
    text = "\n".join(lines)

    chunks = split_for_request(text, 1000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    # No utterance was cut in half.
    assert "\n".join(chunks) == text


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
    client = FakeClient([FakeResponse("前半"), FakeResponse("後半")])

    result = TranscriptRefiner(client=client, max_chars_per_request=1200).refine(MeetingRecord.from_api(payload))

    assert len(client.messages.calls) == 2
    assert result == "前半\n\n後半"


# --- failure handling -------------------------------------------------------


def test_an_api_failure_raises_a_refinement_error():
    client = FakeClient(error=RuntimeError("connection reset"))

    with pytest.raises(RefinementError):
        TranscriptRefiner(client=client).refine(_record())


def test_a_refusal_is_surfaced_rather_than_silently_accepted():
    client = FakeClient([FakeResponse("", stop_reason="refusal")])

    with pytest.raises(RefinementError):
        TranscriptRefiner(client=client).refine(_record())


def test_an_empty_response_is_an_error_not_an_empty_transcript():
    client = FakeClient([FakeResponse("   ")])

    with pytest.raises(RefinementError):
        TranscriptRefiner(client=client).refine(_record())


# --- integration with the record -------------------------------------------


def test_refine_record_attaches_the_cleaned_copy_without_touching_the_raw_one():
    record = _record()
    refined = refine_record(record, TranscriptRefiner(client=FakeClient([FakeResponse("整えた本文")])))

    assert refined.refined_transcript == "整えた本文"
    # The original is never discarded.
    assert [u.text for u in refined.transcript] == [u.text for u in record.transcript]


def test_a_failure_leaves_the_record_deliverable():
    # Losing the conversation because its cleanup failed is the worse outcome.
    record = _record()
    refined = refine_record(record, TranscriptRefiner(client=FakeClient(error=RuntimeError("boom"))))

    assert refined.refined_transcript is None
    assert refined.has_transcript


def test_the_cleaned_copy_survives_the_store_round_trip():
    record = _record().with_refined_transcript("整えた本文")

    restored = MeetingRecord.from_stored(record.to_dict())

    assert restored.refined_transcript == "整えた本文"


def test_raw_rendering_marks_the_user_and_drops_empty_utterances():
    payload = conversation_payload()
    payload["transcript_segments"].append(
        {"id": "blank", "text": "   ", "speaker": "SPEAKER_02", "is_user": False, "start": 20.0, "end": 21.0}
    )

    rendered = render_raw_transcript(MeetingRecord.from_api(payload).transcript)

    assert "自分:" in rendered
    assert "SPEAKER_02" not in rendered


# --- fatal vs transient -----------------------------------------------------


class _AuthlessClient:
    """Mirrors the SDK's behavior when no credential can be resolved: the client
    constructs fine and only the request fails, with a bare TypeError."""

    class _Messages:
        def create(self, **kwargs):
            raise TypeError(
                "Could not resolve authentication method. Expected one of api_key, "
                "auth_token, or credentials to be set."
            )

    messages = _Messages()


def test_missing_credentials_are_reported_as_fatal():
    with pytest.raises(RefinementError) as exc:
        TranscriptRefiner(client=_AuthlessClient()).refine(_record())

    assert exc.value.fatal
    assert "Could not resolve authentication" in str(exc.value)


def test_a_fatal_failure_propagates_out_of_refine_record():
    # So the caller can stop refining instead of repeating the same failed call
    # once per conversation.
    with pytest.raises(RefinementError):
        refine_record(_record(), TranscriptRefiner(client=_AuthlessClient()))


def test_a_transient_failure_is_not_fatal():
    refiner = TranscriptRefiner(client=FakeClient(error=RuntimeError("connection reset by peer")))

    with pytest.raises(RefinementError) as exc:
        refiner.refine(_record())

    assert not exc.value.fatal


def test_the_error_message_carries_the_underlying_detail():
    refiner = TranscriptRefiner(client=FakeClient(error=RuntimeError("connection reset by peer")))

    with pytest.raises(RefinementError) as exc:
        refiner.refine(_record())

    assert "connection reset by peer" in str(exc.value)
