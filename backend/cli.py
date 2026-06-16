# Runnable entry point. 

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from config import build_log_source, load_config


def cmd_analyze(args: argparse.Namespace) -> int:
    from sessions.db import connect, init_schema
    from sessions.models import create_session, get_session, record_analysis, set_session_status

    config = load_config()
    source = build_log_source(config)

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=args.minutes)

    print(f"source:  {config.log_source}")
    print(f"service: {args.service}")
    print(f"window:  {start.isoformat()} -> {end.isoformat()}")

    events = source.fetch(args.service, start, end)
    print(f"fetched: {len(events)} events")

    init_schema()
    with connect() as conn:
        session_id = create_session(conn, args.service, start, end)
        record_analysis(
            conn,
            session_id,
            rank=1,
            composite_score=float(len(events)),
            verdict_line=f"ingested {len(events)} raw events (no ranking yet)",
        )
        set_session_status(conn, session_id, "complete")
        session = get_session(conn, session_id)

    print(f"session: id={session.id} status={session.status}")
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="fetch a window and persist a session")
    p.add_argument("--service", required=True, help="CloudWatch log group name")
    p.add_argument("--minutes", type=int, default=60, help="window size in minutes")
    p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
