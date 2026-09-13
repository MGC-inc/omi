"""Clean up Omi's raw transcript with Claude.

Omi's automatic transcription is usable but noisy: fragments recognized out of
silence, words returned in the wrong script, filler, and `speaker: None` where
diarization did not resolve. The per-conversation summary Omi produces is good;
the transcript underneath it is what needs work before anyone reads it.

This module sends the raw transcript to Claude and asks for a cleaned reading
copy. Two rules shape the prompt and the tests:

* Nothing may be invented. The model removes and reorganizes; it does not add
  content, resolve ambiguity by guessing, or summarize.
* The original is never discarded. The cleaned text is an addition to the
  record, so a reader can always go back to what the device actually heard.

Refinement is off by default: it costs a model call per conversation and needs
an LLM key that the rest of the pipeline does not. Which provider serves it is
llm.py's business, not this module's.
"""

import logging
from typing import List, Optional, Sequence

from .llm import LLMError
from .models import MeetingRecord, Utterance

logger = logging.getLogger(__name__)

# A conversation long enough to exceed this is split, refined in pieces, and
# rejoined. Well under the context window — the limit that matters here is that
# one enormous request is slower and harder to retry than a few smaller ones.
MAX_CHARS_PER_REQUEST = 40000

SYSTEM_PROMPT = """\
あなたは音声の自動文字起こしを、読める記録に整える編集者です。

入力は Omi ウェアラブルの自動文字起こしで、次のノイズを含みます:
- 無音区間から拾われた無意味な断片
- 別言語として誤認識された短い挿入
- 話者の取り違え、話者不明（SPEAKER_00 など）
- 言い淀み、繰り返し

出力の規則:
1. 実際に話された内容だけを残す。**推測で内容を補わない。**
2. 明らかな認識ノイズ（文脈と無関係な断片、意味をなさない一語）は削除する。
3. 言い淀み（えー、あのー、その）と単純な繰り返しは削除してよい。
4. 発言の順序と意味は変えない。要約しない。言い換えを最小限にとどめる。
5. 話者が文脈から区別できる場合のみ「話者A」「話者B」と示す。
   区別できない場合は話者を書かない。実在の人名を推測して当てはめない。
6. 聞き取れていないと判断した箇所は `[不明瞭]` と書く。
7. 出力は日本語の Markdown。見出しは付けず、発言を段落または箇条書きで並べる。
8. 前置き・後書き・説明を書かない。整えた本文だけを返す。

判断に迷ったら、消すのではなく残す側に倒してください。\
"""


#: Kept as an alias so callers and tests written against refinement keep working;
#: every failure now originates in llm.py.
RefinementError = LLMError


class TranscriptRefiner:
    """One model call per conversation (or per chunk of a long one)."""

    def __init__(self, llm, max_chars_per_request: int = MAX_CHARS_PER_REQUEST):
        self._llm = llm
        self._max_chars = max_chars_per_request

    def refine(self, record: MeetingRecord) -> Optional[str]:
        """Return a cleaned reading copy, or None when there is nothing to clean."""
        raw = render_raw_transcript(record.transcript)
        if not raw.strip():
            return None

        chunks = split_for_request(raw, self._max_chars)
        cleaned: List[str] = []
        for index, chunk in enumerate(chunks, start=1):
            cleaned.append(self._llm.complete_text(SYSTEM_PROMPT, chunk))
        return "\n\n".join(part for part in cleaned if part.strip()) or None


def render_raw_transcript(transcript: Sequence[Utterance]) -> str:
    """The raw transcript as the model sees it: timestamps, speaker, text."""
    lines = []
    for utterance in transcript:
        if not utterance.text.strip():
            continue
        total = max(0, int(utterance.start_seconds))
        speaker = "自分" if utterance.is_user else (utterance.speaker or "話者不明")
        lines.append("[{:02d}:{:02d}] {}: {}".format(total // 60, total % 60, speaker, utterance.text))
    return "\n".join(lines)


def split_for_request(text: str, max_chars: int) -> List[str]:
    """Split on line boundaries so an utterance is never cut in half."""
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for line in text.split("\n"):
        # +1 for the newline that rejoins it.
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def refine_record(record: MeetingRecord, refiner: TranscriptRefiner) -> MeetingRecord:
    """Attach a cleaned transcript, or return the record untouched.

    A refinement failure is logged and swallowed: the raw transcript is still
    delivered, which is harder to read but never wrong. Losing the conversation
    because its cleanup failed would be the worse outcome.

    Raises RefinementError only when the failure is fatal — the caller should
    then stop refining for the rest of the run rather than repeat it.
    """
    if not record.has_transcript:
        return record
    try:
        cleaned = refiner.refine(record)
    except RefinementError as exc:
        if exc.fatal:
            raise
        logger.warning("%s", exc)
        return record
    if not cleaned:
        return record
    return record.with_refined_transcript(cleaned)
