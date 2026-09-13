"""The contract every delivery target implements."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ..daily import DailySummary
    from ..models import MeetingRecord


class SinkError(RuntimeError):
    """Delivery to one sink failed. The pipeline records it and moves on."""


class Sink:
    """Deliver one conversation to one destination.

    ``deliver`` must be safe to call twice with the same record: a run that
    crashes after delivering but before saving state will retry it.
    """

    name = "sink"

    #: Whether this destination also accepts the day's review. A sink that
    #: leaves this False is simply skipped by the daily command.
    supports_daily = False

    def deliver(self, record: "MeetingRecord") -> None:
        raise NotImplementedError

    def deliver_daily(self, summary: "DailySummary") -> None:
        """Deliver the day's roll-up. Only called when supports_daily is True.

        Like ``deliver``, it must be safe to call twice for the same day: the
        daily command is expected to be re-run.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any held resources. Called once at the end of a run."""
