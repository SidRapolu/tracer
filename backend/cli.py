from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from config import build_embedder, build_log_source, load_config


def cmd_analyze(args: argparse.Namespace) -> int:
    import dataclasses

    from sessions.db import connect, init_schema
    from sessions.models import create_session, get_session, set_session_status
    from signatures.store import ingest_signatures

    config = load_config()
    # The CLI defaults to the fixture source so a routine "analyze" call never hits
    # real AWS by reflex; pass --source cloudwatch to opt into live logs.
    config = dataclasses.replace(config, log_source=args.source)

    source = build_log_source(config)
    embedder = build_embedder(config)

    end = datetime.now(timezone.utc)
    if args.all:
        # Wide-open window: process everything the source returns. Used for the
        # fixture source, whose events sit at fixed past dates.
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        start = end - timedelta(minutes=args.minutes)

    print(f"source:  {config.log_source}")
    print(f"service: {args.service}")
    print(f"window:  {start.isoformat()} -> {end.isoformat()}")

    events = source.fetch(args.service, start, end)
    print(f"fetched: {len(events)} events")

    init_schema()
    with connect() as conn:
        session_id = create_session(conn, args.service, start, end)
        summary = ingest_signatures(conn, embedder, args.service, session_id, events)
        set_session_status(conn, session_id, "complete")
        session = get_session(conn, session_id)

    print(f"signatures: {summary['distinct']} distinct, {summary['novel']} novel (embedded)")
    print(f"session: id={session.id} status={session.status}")
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="analyze a window of logs into signatures")
    p.add_argument("--service", required=True, help="CloudWatch log group name")
    p.add_argument("--minutes", type=int, default=60, help="window size in minutes")
    p.add_argument("--all", action="store_true", help="ignore the window; process all events (fixture source)")
    p.add_argument(
        "--source",
        choices=["fixture", "cloudwatch"],
        default="fixture",
        help="log source; defaults to fixture so live AWS is never hit by reflex",
    )
    p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
