"""Command-line entry point: ``python -m meeting_digest [command]``.

Commands:
  ingest   fetch new conversations and deliver them (the default)
  daily    build and deliver one day's review
  serve    run the read-only HTTP API over the local store

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
from .config import Config, ConfigError, load_env_file
from .daily import resolve_day
from .pipeline import run, run_daily
from .sinks import build_sinks
from .sinks.base import Sink
from .state import DeliveryState, StateError

logger = logging.getLogger("meeting_digest")

COMMANDS = ("ingest", "daily", "serve")
TOP_LEVEL_FLAGS = ("-h", "--help", "--version")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose, args.json)

    # A .env beside the working directory is the ordinary place to keep the key;
    # real environment variables still take precedence over it.
    load_env_file(args.env_file)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2

    if args.show_config:
        print(json.dumps(config.redacted(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        # The API reads the local store; it needs no sinks and no Omi call.
        from .server import ServerConfigError, serve

        try:
            return serve(config)
        except ServerConfigError as exc:
            print("server configuration error: {}".format(exc), file=sys.stderr)
            return 2

    try:
        sinks = build_sinks(config)
    except ConfigError as exc:
        print("startup error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        if args.command == "daily":
            return _run_daily(config, sinks, args)
        return _run_ingest(config, sinks, args)
    finally:
        _close_all(sinks)


def _run_ingest(config: Config, sinks: List[Sink], args) -> int:
    try:
        state = DeliveryState.load(config.state_path)
    except StateError as exc:
        print("startup error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        with OmiClient(config) as client:
            summary = run(config, client, sinks, state)
    except OmiApiError as exc:
        print("api error: {}".format(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            "listed={} already_delivered={} skipped_short={} fetched={} delivered={} deferred={}".format(
                summary.listed,
                summary.already_delivered,
                summary.skipped_short,
                summary.fetched,
                summary.delivered,
                summary.deferred,
            )
        )
        if summary.rate_limited:
            print("rate limited — the remainder is queued for the next run")

    for failure in summary.failures:
        print("FAILED: {}".format(failure), file=sys.stderr)
    return 0 if not summary.failures else 1


def _run_daily(config: Config, sinks: List[Sink], args) -> int:
    try:
        day = resolve_day(args.day, config.utc_offset_hours)
    except ValueError as exc:
        print("argument error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        with OmiClient(config) as client:
            summary = run_daily(config, client, sinks, day)
    except OmiApiError as exc:
        print("api error: {}".format(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            "day={} listed={} in_day={} delivered_to={}".format(
                summary.day, summary.listed, summary.in_day, ",".join(summary.delivered_to) or "-"
            )
        )
        if summary.skipped_sinks:
            print("sinks without a daily view: {}".format(", ".join(summary.skipped_sinks)))

    for failure in summary.failures:
        print("FAILED: {}".format(failure), file=sys.stderr)
    return 0 if not summary.failures else 1


def _close_all(sinks: List[Sink]) -> None:
    for sink in sinks:
        try:
            sink.close()
        except Exception:  # closing must not mask the run's own outcome
            logger.debug("sink %s failed to close", sink.name, exc_info=True)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    # `python -m meeting_digest --json` keeps working: a leading flag that is not
    # a top-level one belongs to the default command.
    if not raw or (raw[0].startswith("-") and raw[0] not in TOP_LEVEL_FLAGS):
        raw = ["ingest"] + raw

    parser = argparse.ArgumentParser(
        prog="meeting_digest",
        description="Pull Omi conversations and deliver them to the configured sinks.",
    )
    parser.add_argument("--version", action="version", version="meeting_digest {}".format(__version__))
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("ingest", "Fetch new conversations and deliver each one."),
        ("daily", "Build and deliver one day's review."),
        ("serve", "Run the read-only HTTP API over the local store."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
        sub.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
        sub.add_argument(
            "--show-config",
            action="store_true",
            help="Print the resolved configuration (the API key is redacted) and exit.",
        )
        sub.add_argument(
            "--env-file",
            default=None,
            help="Path to a .env file (default: ./.env, or $MD_ENV_FILE).",
        )
        if name == "daily":
            sub.add_argument(
                "--day",
                default="yesterday",
                help="Which day to review: an ISO date (YYYY-MM-DD), 'today', or 'yesterday' (default).",
            )

    args = parser.parse_args(raw)
    if not hasattr(args, "day"):
        args.day = None
    return args


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
