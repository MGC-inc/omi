"""The contract every delivery target implements."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ..models import MeetingRecord


class SinkError(RuntimeError):
    """Delivery to one sink failed. The pipeline records it and moves on."""


class Sink:
    """Deliver one conversation to one destination.

    ``deliver`` must be safe to call twice with the same record: a run that
    crashes after delivering but before saving state will retry it.
    """

    name = "sink"

    def deliver(self, record: "MeetingRecord") -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Release any held resources. Called once at the end of a run."""
