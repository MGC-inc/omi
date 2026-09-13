"""Mirror each conversation into the local SQLite store.

Enable this sink when other internal tools should be able to read Omi data
through the HTTP API (``meeting_digest.server``) instead of holding an Omi key
of their own. The pipeline pays the Developer API budget once; every consumer
then reads a local copy.
"""

from ..models import MeetingRecord
from ..store import ConversationStore, StoreError
from .base import Sink, SinkError


class StoreSink(Sink):
    name = "store"

    def __init__(self, store: ConversationStore):
        self._store = store
        try:
            store.initialize()
        except StoreError as exc:
            raise SinkError("store: {}".format(exc))

    def deliver(self, record: MeetingRecord) -> None:
        try:
            # An upsert, so a repeated delivery replaces rather than duplicates.
            self._store.upsert(record)
        except StoreError as exc:
            raise SinkError("store: {}".format(exc))
