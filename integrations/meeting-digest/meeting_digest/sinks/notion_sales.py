"""Route sales-specific knowledge to its own Notion database.

This is not a second copy of the general meeting log. A conversation only
lands here when the speaker used a sales-specific clip phrase (see
``curate.DEFAULT_SALES_CLIP_PHRASES``) — ``record.sales_tagged`` is the signal,
set by ``Curation.sales`` through ``MeetingRecord.with_curation``. Every other
conversation is a silent no-op: nothing is written twice, and the general
``NotionSink`` (if also configured) still receives it as usual.

Transcripts are always excluded here, regardless of configuration: a
sales-knowledge database is meant to hold the objection, the need, or the
commitment that was deliberately marked — not a second transcript archive.
"""

from .base import SinkError  # re-exported for callers that only import from here
from .notion import NotionSink

__all__ = ["NotionSalesSink", "SinkError"]


class NotionSalesSink(NotionSink):
    name = "notion_sales"
    #: A day's review mixes every conversation, sales-tagged or not; writing
    #: it here would defeat the point of a sales-only destination. Daily
    #: roll-ups stay the general log's job.
    supports_daily = False

    def __init__(self, *args, **kwargs):
        kwargs["include_transcript"] = False
        super().__init__(*args, **kwargs)

    def deliver(self, record) -> None:
        if not record.sales_tagged:
            return
        super().deliver(record)
