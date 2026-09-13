"""Sample Developer API payloads.

Field names and shapes mirror backend/models/conversation.py and
backend/models/structured.py as of the version this pipeline was written
against. If an upstream rename breaks normalization, these are what fail.
"""

from typing import Any, Dict, Optional


def conversation_payload(
    conversation_id: str = "conv_001",
    started_at: str = "2026-09-10T01:00:00Z",
    finished_at: str = "2026-09-10T01:45:00Z",
    with_transcript: bool = True,
    title: str = "A社との商談",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": conversation_id,
        "created_at": started_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "source": "omi",
        "language": "ja",
        "structured": {
            "title": title,
            "overview": "新規導入の初回商談。価格と導入時期を確認した。",
            "emoji": "💼",
            "category": "business",
            "sections": [
                {
                    "heading": "先方の要望",
                    "body_markdown": "- 既存システムとの連携が前提\n- 年内に試験導入したい",
                    "source_segment_ids": ["seg_1"],
                }
            ],
            "action_items": [
                {
                    "description": "見積書を送付する",
                    "completed": False,
                    "due_at": "2026-09-15T09:00:00Z",
                    "owner_name": "松尾",
                    "context": "先方の稟議が月内締め",
                },
                {"description": "議事録を共有する", "completed": True},
            ],
            "events": [
                {
                    "title": "次回打ち合わせ",
                    "description": "技術要件のすり合わせ",
                    "start": "2026-09-20T05:00:00Z",
                    "duration": 60,
                }
            ],
        },
    }
    if with_transcript:
        payload["transcript_segments"] = [
            {
                "id": "seg_1",
                "text": "本日はお時間をいただきありがとうございます。",
                "speaker": "SPEAKER_00",
                "is_user": True,
                "start": 0.0,
                "end": 3.5,
            },
            {
                "id": "seg_2",
                "text": "こちらこそ。予算は上限が決まっておりまして。",
                "speaker": "SPEAKER_01",
                "is_user": False,
                "start": 3.5,
                "end": 9.0,
            },
        ]
    return payload


def list_item(conversation_id: str, started_at: str, title: Optional[str] = None) -> Dict[str, Any]:
    """What the list endpoint returns with include_transcript=False."""
    payload = conversation_payload(
        conversation_id=conversation_id,
        started_at=started_at,
        with_transcript=False,
        title=title or "会話 {}".format(conversation_id),
    )
    return payload
