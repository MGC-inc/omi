"""Command-line entry point: ``python -m meeting_digest``.

Exit codes:
  0  the run finished; everything it attempted was delivered (a run that hit the
     transcript budget or a rate limit still exits 0 — the rest is queued)
  1  at least one delivery or fetch failed
  2  the configuration is unusable
"""

import argparse
import json
import logging
import sys
from typing import List, Optional, Sequence

from . import __version__
from .client import OmiApiError, OmiClient
from .config import Config, ConfigError
from .pipeline import run
from .sinks import build_sinks
from .sinks.base import Sink
from .state import DeliveryState, StateError

logger = logging.getLogger("meeting_digest")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose, args.json)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2

    if args.show_config:
        print(json.dumps(config.redacted(), ensure_ascii=False, indent=2))
        return 0

    try:
        state = DeliveryState.load(config.state_path)
        sinks = build_sinks(config)
    except (ConfigError, StateError) as exc:
        print("startup error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        with OmiClient(config) as client:
            summary = run(config, client, sinks, state)
    except OmiApiError as exc:
        print("api error: {}".format(exc), file=sys.stderr)
        return 1
    finally:
        _close_all(sinks)

    if args.json:
        print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_summary(summary)

    return 0 if not summary.failures else 1


def _print_summary(summary) -> None:
    print(
        "listed={} already_delivered={} fetched={} delivered={} deferred={}".format(
            summary.listed, summary.already_delivered, summary.fetched, summary.delivered, summary.deferred
        )
    )
    if summary.rate_limited:
        print("rate limited — the remainder is queued for the next run")
    for failure in summary.failures:
        print("FAILED: {}".format(failure), file=sys.stderr)


def _close_all(sinks: List[Sink]) -> None:
    for sink in sinks:
        try:
            sink.close()
        except Exception:  # closing must not mask the run's own outcome
            logger.debug("sink %s failed to close", sink.name, exc_info=True)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="meeting_digest",
        description="Pull new Omi conversations and deliver them to the configured sinks.",
    )
    parser.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print the resolved configuration (the API key is redacted) and exit.",
    )
    parser.add_argument("--version", action="version", version="meeting_digest {}".format(__version__))
    return parser.parse_args(argv)


def _configure_logging(verbose: bool, json_output: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr if json_output else sys.stdout,
    )
    # httpx logs every request at INFO, which buries this pipeline's own lines.
    # Under --verbose it stays on, where the request trace is the point.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


if __name__ == "__main__":
    sys.exit(main())
