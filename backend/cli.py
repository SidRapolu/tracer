"""
cli.py

Runnable entry point. The analyze pipeline: fetch a window of logs for a
service, normalize them into per-service signatures, embed novel signatures
via Titan (lazily, only on an exact-match miss), and persist signatures plus
per-session occurrence counts to Postgres.

    python backend/cli.py analyze --service /aws/lambda/checkout --all

The CLI defaults to the fixture source, so the command above never touches
real AWS. Use --all to ignore the time window and process every event (the
fixture's events sit at fixed past dates a "last N minutes" window misses).
To run against live CloudWatch, opt in explicitly:

    python backend/cli.py analyze --service /aws/lambda/your-function \
        --minutes 60 --source cloudwatch
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from config import build_embedder, build_log_source, load_config


def _parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp, tolerating a trailing Z and naive values."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cmd_analyze(args: argparse.Namespace) -> int:
    import dataclasses

    from sessions.db import connect, init_schema
    from sessions.models import create_session, get_session, set_session_status
    from signatures.store import ingest_signatures

    config = load_config()
    # The CLI defaults to the fixture source so a routine `analyze` never hits
    # real AWS by reflex; pass --source cloudwatch to opt into live logs. This
    # is a deliberate split: the library default (config.py) stays online-first
    # for the product, while the dev command defaults to safe.
    config = dataclasses.replace(config, log_source=args.source)

    source = build_log_source(config)
    embedder = build_embedder(config)

    if args.start or args.end:
        # Explicit ISO window — used to run a fixture's baseline hour and
        # incident hour as separate sessions.
        start = _parse_iso(args.start) if args.start else datetime(1970, 1, 1, tzinfo=timezone.utc)
        end = _parse_iso(args.end) if args.end else datetime.now(timezone.utc)
    elif args.all:
        # Wide-open window: process everything the source returns.
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=args.minutes)

    print(f"source:  {config.log_source}")
    print(f"service: {args.service}")
    print(f"window:  {start.isoformat()} -> {end.isoformat()}")

    events = source.fetch(args.service, start, end)
    print(f"fetched: {len(events)} events")

    init_schema()
    with connect() as conn:
        is_baseline = args.kind == "baseline"
        session_id = create_session(conn, args.service, start, end, kind=args.kind)
        summary = ingest_signatures(
            conn, embedder, args.service, session_id, events,
            skip_high_severity=is_baseline,
        )

        if is_baseline:
            from ranking.baseline import prune_baseline_sessions

            prune_baseline_sessions(conn, args.service)

        candidates = []
        verdicts = []
        if args.kind == "incident":
            from ranking.rank import rank_session

            candidates = rank_session(conn, args.service, session_id)

            if not args.no_verdicts and candidates:
                from config import build_verdict_generator
                from verdicts.orchestrate import generate_verdicts

                generator = build_verdict_generator(config)
                verdicts = generate_verdicts(conn, generator, session_id, top_n=3)

        set_session_status(conn, session_id, "complete")
        session = get_session(conn, session_id)

    print(f"signatures: {summary['distinct']} distinct, {summary['novel']} novel (embedded)")
    print(f"session: id={session.id} kind={session.kind} status={session.status}")

    if candidates:
        print("\nranked candidates:")
        for rank, c in enumerate(candidates, start=1):
            print(
                f"  {rank}. [{c.composite:.3f}] {c.template}"
                f"  (n={c.incident_count} nov={c.novelty:.2f}"
                f" rate={c.rate_change:.2f} sev={c.severity:.2f})"
            )

    if verdicts:
        print("\nverdicts (top 3):")
        for v in verdicts:
            print(f"  #{v['rank']} {v['template']}")
            print(f"     verdict:   {v['verdict_line']}")
            print(f"     hypothesis: {v['hypothesis']}")
            print(f"     next step:  {v['next_step']}")

    if args.kind == "baseline":
        print("(baseline run — signatures recorded, no ranking)")

    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="analyze a window of logs into signatures")
    p.add_argument("--service", required=True, help="CloudWatch log group name")
    p.add_argument("--minutes", type=int, default=60, help="window size in minutes")
    p.add_argument("--all", action="store_true", help="ignore the window; process all events (fixture source)")
    p.add_argument("--start", help="ISO start of window, e.g. 2026-05-01T11:00:00Z (overrides --minutes/--all)")
    p.add_argument("--end", help="ISO end of window, e.g. 2026-05-01T12:00:00Z")
    p.add_argument(
        "--source",
        choices=["fixture", "cloudwatch"],
        default="fixture",
        help="log source; defaults to fixture so live AWS is never hit by reflex",
    )
    p.add_argument(
        "--kind",
        choices=["baseline", "incident"],
        default="incident",
        help="tag the run as baseline (known-quiet history) or incident (default)",
    )
    p.add_argument(
        "--no-verdicts",
        action="store_true",
        help="skip the Bedrock verdict layer (ranking only, no Claude calls)",
    )
    p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
