import pytest

from meeting_digest.curate import (
    DEFAULT_CLIP_PHRASES,
    Curation,
    Curator,
    find_clip_phrase,
    render_for_curation,
)
from meeting_digest.llm import LLMError
from meeting_digest.models import MeetingRecord
from tests.fake_llm import FakeLLM, fatal, transient
from tests.fixtures import conversation_payload


def _record(**kwargs) -> MeetingRecord:
    return MeetingRecord.from_api(conversation_payload(**kwargs))


def _with_speech(text: str) -> MeetingRecord:
    payload = conversation_payload()
    payload["transcript_segments"] = [
        {"id": "s1", "text": text, "speaker": "SPEAKER_00", "is_user": True, "start": 0.0, "end": 5.0}
    ]
    return MeetingRecord.from_api(payload)


# --- spoken triggers --------------------------------------------------------


def test_a_clip_phrase_keeps_the_conversation_without_asking_the_model():
    # A deliberate mark outranks any judgement a model would make — and costs
    # nothing, which matters when this runs over every conversation.
    llm = FakeLLM(json_responses=[{"worth_keeping": False, "reason": "つまらない", "ideas": [], "topics": []}])

    curation = Curator(llm).curate(_with_speech("これは後で使えそう。クリップ。"))

    assert curation.worth_keeping
    assert curation.clipped
    assert llm.calls == []


def test_clip_phrases_are_configurable():
    llm = FakeLLM()

    curation = Curator(llm, clip_phrases=["ブックマーク"]).curate(_with_speech("ここ、ブックマークしておいて"))

    assert curation.clipped
    assert "ブックマーク" in curation.reason


def test_the_default_phrases_include_japanese_and_english():
    assert "クリップ" in DEFAULT_CLIP_PHRASES
    assert "clip this" in DEFAULT_CLIP_PHRASES


def test_clip_matching_is_case_insensitive():
    assert find_clip_phrase(_with_speech("OK, CLIP THIS one"), ["clip this"]) == "clip this"


def test_a_conversation_without_a_trigger_goes_to_the_model():
    llm = FakeLLM()

    Curator(llm).curate(_with_speech("今日はいい天気ですね"))

    assert len(llm.calls) == 1


# --- the judgement ----------------------------------------------------------


def test_the_prompt_forbids_inventing_ideas():
    # Curation decides what survives. A model that invents here fabricates the
    # record itself, not just a summary of it.
    llm = FakeLLM()
    Curator(llm).curate(_with_speech("なにか話した"))

    system = llm.calls[0]["system"]
    assert "会話に無いことを書かない" in system
    assert "無理に絞り出さない" in system
    assert "残す側に倒す" in system


def test_what_the_model_reads_includes_the_summary_and_the_transcript():
    llm = FakeLLM()
    Curator(llm).curate(_record())

    sent = llm.calls[0]["user"]
    assert "新規導入の初回商談" in sent  # Omi's own summary
    assert "本日はお時間をいただきありがとうございます。" in sent  # the transcript


def test_extracted_ideas_are_attached():
    llm = FakeLLM(
        json_responses=[
            {
                "worth_keeping": True,
                "reason": "アイデアあり",
                "ideas": ["代理店経由の方が返信率が高い", "年内に試験導入を狙う"],
                "topics": ["営業", "代理店"],
            }
        ]
    )

    curation = Curator(llm).curate(_with_speech("なにか話した"))

    assert curation.ideas == ["代理店経由の方が返信率が高い", "年内に試験導入を狙う"]
    assert curation.topics == ["営業", "代理店"]


def test_an_idea_overrides_a_false_worth_keeping_flag():
    # If the model extracted something, that extraction is the evidence.
    llm = FakeLLM(json_responses=[{"worth_keeping": False, "reason": "短い", "ideas": ["使える発見"], "topics": []}])

    assert Curator(llm).curate(_with_speech("x")).worth_keeping


def test_a_conversation_with_nothing_in_it_is_dropped():
    llm = FakeLLM(json_responses=[{"worth_keeping": False, "reason": "雑談のみ", "ideas": [], "topics": []}])

    curation = Curator(llm).curate(_with_speech("えーっと、はい、そうですね"))

    assert not curation.worth_keeping
    assert curation.reason == "雑談のみ"


def test_topics_are_capped_at_three():
    llm = FakeLLM(
        json_responses=[{"worth_keeping": True, "reason": "", "ideas": [], "topics": ["a", "b", "c", "d", "e"]}]
    )

    assert len(Curator(llm).curate(_with_speech("x")).topics) == 3


# --- failure handling -------------------------------------------------------


def test_a_transient_failure_keeps_the_conversation():
    # A curation failure must never be the reason something is lost.
    curation = Curator(FakeLLM(error=transient())).curate(_with_speech("なにか話した"))

    assert curation.worth_keeping
    assert "失敗" in curation.reason


def test_a_fatal_failure_propagates():
    with pytest.raises(LLMError):
        Curator(FakeLLM(error=fatal())).curate(_with_speech("なにか話した"))


def test_an_empty_conversation_is_dropped_without_a_call():
    llm = FakeLLM()
    empty = MeetingRecord.from_api({"id": "x", "created_at": "2026-09-13T01:00:00Z"})

    curation = Curator(llm).curate(empty)

    assert not curation.worth_keeping
    assert llm.calls == []


# --- the record -------------------------------------------------------------


def test_curation_attaches_to_the_record_and_survives_the_store_round_trip():
    record = _record().with_curation(
        Curation(worth_keeping=True, reason="アイデアあり", ideas=["発見"], topics=["営業"], clipped=True)
    )

    restored = MeetingRecord.from_stored(record.to_dict())

    assert restored.ideas == ["発見"]
    assert restored.topics == ["営業"]
    assert restored.curation_reason == "アイデアあり"
    assert restored.clipped


# --- degraded operation without an LLM --------------------------------------


def test_without_an_llm_spoken_triggers_still_work():
    # A missing key should cost the filtering, never the conversation the
    # speaker deliberately marked.
    curation = Curator(None).curate(_with_speech("これは残したい。クリップ。"))

    assert curation.clipped
    assert curation.worth_keeping


def test_without_an_llm_nothing_is_filtered_out():
    curation = Curator(None).curate(_with_speech("えーっと、はい、そうですね"))

    assert curation.worth_keeping
    assert not curation.clipped
    assert "LLM 未設定" in curation.reason
