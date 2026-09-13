"""Decide what is worth keeping, and pull the ideas out of it.

The pipeline's original shape was "store everything and search later". The
community usage data says otherwise: the single most-used Omi plugin is one
that records **only** the span between two spoken trigger phrases and ignores
every other conversation. What people pay for is not a complete archive — it is
not losing the thing they just said.

So two paths reach the sinks:

* **Clipped** — the speaker said a trigger phrase ("クリップ"). Kept
  unconditionally; a deliberate mark is never second-guessed by a model.
* **Curated** — everything else is read once by an LLM, which decides whether
  it carries an idea, a decision, a commitment, or a fact worth keeping, and
  extracts those. Conversations that carry none are not delivered at all.

The model removes and extracts; it never invents. An idea that was not said
does not become an idea because a model found the conversation dull.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .llm import LLMError
from .models import MeetingRecord

logger = logging.getLogger(__name__)

#: Spoken marks that pin a conversation. Japanese first — this is what the
#: operator actually says — with the English phrases the upstream plugin uses.
DEFAULT_CLIP_PHRASES = ("クリップ", "これメモ", "メモして", "記録して", "clip this", "note this")

CURATION_SCHEMA = {
    "type": "object",
    "properties": {
        "worth_keeping": {
            "type": "boolean",
            "description": "後で失うと惜しい情報が含まれているか",
        },
        "reason": {
            "type": "string",
            "description": "その判断の理由を一文で",
        },
        "ideas": {
            "type": "array",
            "items": {"type": "string"},
            "description": "会話から拾ったアイデア・気づき・決定事項。無ければ空",
        },
        "topics": {
            "type": "array",
            "items": {"type": "string"},
            "description": "話題を表す短い語。3件まで",
        },
    },
    "required": ["worth_keeping", "reason", "ideas", "topics"],
}

SYSTEM_PROMPT = """\
あなたは、日常の会話から「後で失うと惜しい情報」を拾い出す編集者です。

入力はウェアラブル端末が自動で記録した会話です。自動音声認識のため、無音区間の
誤認識や別言語として認識された断片が混ざります。それらは無視してください。

## 残す価値があるもの

- 新しいアイデア、企画の種、「こうしたらいいかも」という思いつき
- 決定事項、約束、次にやること
- 相手から得た事実（予算・決裁の流れ・時期・人の名前と役割・制約）
- 後で思い出せないと困る気づき

## 残す価値がないもの

- 雑談、相槌、儀礼的なやりとり
- 認識ノイズ、意味をなさない断片
- すでに何度も出ている話の単なる繰り返し

## 出力の規則

1. `ideas` には拾った項目を1件ずつ、**会話で実際に語られた内容だけ**を短く書く。
   会話に無いことを書かない。要約ではなく、その項目そのものを書く。
2. 拾うものが無ければ `ideas` は空配列にする。無理に絞り出さない。
3. `worth_keeping` は、`ideas` が1件以上あるか、決定・約束・重要な事実が
   含まれる場合に true。会話が短くても、中身があれば true にする。
4. `reason` はその判断の理由を一文で。
5. `topics` は話題を表す短い語を最大3件。
6. 判断に迷ったら **残す側に倒す**。捨てた情報は戻らない。\
"""


@dataclass(frozen=True)
class Curation:
    worth_keeping: bool
    reason: str = ""
    ideas: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    #: True when a spoken trigger phrase decided it, with no model involved.
    clipped: bool = False

    @staticmethod
    def from_clip(phrase: str) -> "Curation":
        return Curation(
            worth_keeping=True,
            reason="発話トリガー「{}」で指定されました".format(phrase),
            clipped=True,
        )


class Curator:
    """Judges conversations. Works without an LLM, in a reduced form.

    ``llm=None`` leaves spoken triggers working — they never needed a model —
    while everything else is kept unjudged. That is the right degradation: a
    missing key should cost the filtering, never the conversations, and least of
    all the ones the speaker deliberately marked.
    """

    def __init__(self, llm=None, clip_phrases: Sequence[str] = DEFAULT_CLIP_PHRASES):
        self._llm = llm
        self._clip_phrases = [p for p in clip_phrases if p.strip()]

    def curate(self, record: MeetingRecord) -> Curation:
        """Judge one conversation. Raises LLMError only when the failure is fatal."""
        phrase = find_clip_phrase(record, self._clip_phrases)
        if phrase:
            # A deliberate mark outranks any judgement a model would make.
            return Curation.from_clip(phrase)

        if self._llm is None:
            # Triggers still work; nothing else is filtered out.
            return Curation(worth_keeping=True, reason="判定なし（LLM 未設定）")

        if not has_content(record):
            # Nothing to judge, and nothing to pay a model to judge.
            return Curation(worth_keeping=False, reason="内容がありません")

        source = render_for_curation(record)

        try:
            payload = self._llm.complete_json(SYSTEM_PROMPT, source, CURATION_SCHEMA)
        except LLMError as exc:
            if exc.fatal:
                raise
            # An unreadable conversation is kept, not dropped: a curation
            # failure must never be the reason something is lost.
            logger.warning("curation failed for %s, keeping it: %s", record.id, exc)
            return Curation(worth_keeping=True, reason="判定に失敗したため保持しました")

        return _from_payload(payload)


def find_clip_phrase(record: MeetingRecord, phrases: Sequence[str]) -> Optional[str]:
    """The first trigger phrase spoken in this conversation, if any."""
    haystack = " ".join(u.text for u in record.transcript)
    if not haystack:
        haystack = "{} {}".format(record.title, record.overview)
    lowered = haystack.lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            return phrase
    return None


def has_content(record: MeetingRecord) -> bool:
    """Whether there is anything here to judge.

    `title` is never empty — it falls back to "(untitled)" — so it cannot stand
    in for content on its own.
    """
    if record.transcript or record.action_items:
        return True
    if record.overview.strip():
        return True
    return bool(record.title.strip()) and record.title != "(untitled)"


def render_for_curation(record: MeetingRecord) -> str:
    """What the model reads: the summary Omi produced, plus the raw transcript."""
    parts = ["タイトル: {}".format(record.title)]
    if record.overview:
        parts.append("Omi の要約: {}".format(record.overview))
    if record.duration_minutes:
        parts.append("長さ: {}分".format(record.duration_minutes))

    if record.action_items:
        parts.append("")
        parts.append("Omi が抽出したアクション:")
        parts.extend("- {}".format(item.description) for item in record.action_items if item.description)

    if record.transcript:
        parts.append("")
        parts.append("文字起こし:")
        for utterance in record.transcript:
            if utterance.text.strip():
                speaker = "自分" if utterance.is_user else (utterance.speaker or "話者不明")
                parts.append("{}: {}".format(speaker, utterance.text))

    return "\n".join(parts)


def _from_payload(payload: Dict[str, Any]) -> Curation:
    ideas = [str(x).strip() for x in payload.get("ideas") or [] if str(x).strip()]
    topics = [str(x).strip() for x in payload.get("topics") or [] if str(x).strip()][:3]
    keeping = bool(payload.get("worth_keeping"))
    # An extracted idea is itself the reason to keep it, whatever the flag says.
    if ideas:
        keeping = True
    return Curation(
        worth_keeping=keeping,
        reason=str(payload.get("reason") or "").strip(),
        ideas=ideas,
        topics=topics,
    )
