"""
CLI for tracer.

Subcommands:
  analyze   Pull a log window and run signature + candidate scoring.
  history   List recent analyze sessions.
  inspect   Show ranked candidates for a session.
  init-db   Initialize the SQLite schema (idempotent).
"""

import argparse
import json
import sys
from pathlib import Path

from . import baseline, candidates, ingest, signatures, storage
from .fake_aws import FakeLogsClient

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "sample_logs.json"


def _get_client(fake: bool, profile: str):
    if fake:
        return FakeLogsClient(FIXTURE_PATH)
    import boto3
    return boto3.Session(profile_name=profile).client("logs")


def cmd_analyze(args) -> int:
    storage.init_db()
    client = _get_client(args.fake, args.profile)

    start_ms, end_ms = ingest.window_from_args(args.window, args.ago)
    session_id, events = ingest.fetch_logs(
        client, args.log_group, start_ms, end_ms
    )

    if not events:
        print(f"No events found in {args.window}-min window for "
              f"{args.log_group}. Try widening the window or shifting "
              f"--ago.")
        return 0

    with storage.connect() as conn:
        storage.create_session(
            conn, session_id, args.log_group, start_ms, end_ms
        )
        # Insert raw events first so they have IDs.
        storage.insert_events(conn, session_id, args.log_group, events)
        storage.update_session_count(conn, session_id, len(events))

        # Reload events with their assigned ids, attach signatures, persist.
        stored_events = storage.events_for_session(conn, session_id)
        signaturized = signatures.signaturize(stored_events)
        session_counts = baseline.record_session_signatures(
            conn, session_id, args.log_group, signaturized
        )

        ranked = candidates.score_session(
            conn, session_id, args.log_group, session_counts
        )

    _print_analysis(session_id, args.log_group, start_ms, end_ms,
                    len(events), ranked, args.json)
    return 0


def _print_analysis(session_id: str, log_group: str,
                    start_ms: int, end_ms: int, event_count: int,
                    ranked: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "session_id": session_id,
            "log_group": log_group,
            "window_start_ms": start_ms,
            "window_end_ms": end_ms,
            "event_count": event_count,
            "candidates": ranked,
        }, indent=2))
        return

    print()
    print(f"Session: {session_id}")
    print(f"Log group: {log_group}")
    print(f"Window: {(end_ms - start_ms) // 60000} min "
          f"ending {end_ms}")
    print(f"Events pulled: {event_count}")
    print()

    if not ranked:
        print("No candidates surfaced. Either nothing looked anomalous, "
              "or this is the first session and there's no baseline yet.")
        return

    print(f"Top {len(ranked)} candidates (ranked by total score):")
    print()
    for c in ranked:
        prior = (f"seen in {c['sessions_prior']} prior sessions, "
                 f"avg {c['baseline_avg']:.1f}/session"
                 if c["sessions_prior"] > 0
                 else "never seen before")
        sig = c["signature_text"]
        if len(sig) > 90:
            sig = sig[:87] + "..."
        print(f"  #{c['rank']}  [{c['severity']:5s}] "
              f"score={c['total_score']:.2f} "
              f"(nov={c['novelty_score']:.2f}, "
              f"rate={c['rate_score']:.2f}, "
              f"sev={c['severity_score']:.2f})")
        print(f"        count this session: {c['session_count']}  "
              f"({prior})")
        print(f"        {sig}")
        print()



def cmd_history(args) -> int:
    storage.init_db()
    with storage.connect() as conn:
        sessions = storage.list_sessions(conn, args.log_group, args.limit)
    if not sessions:
        print("No sessions recorded yet. Run `tracer analyze` first.")
        return 0
    for s in sessions:
        print(f"  {s['id']}  "
              f"group={s['log_group']}  "
              f"events={s['event_count']}  "
              f"window={(s['window_end_ms'] - s['window_start_ms']) // 60000}m")
    return 0



def cmd_inspect(args) -> int:
    storage.init_db()
    with storage.connect() as conn:
        ranked = storage.candidates_for_session(conn, args.session_id)
    if not ranked:
        print(f"No candidates found for session {args.session_id}.")
        return 1
    print(f"Candidates for session {args.session_id}:")
    print()
    for c in ranked:
        sig = c["signature_text"]
        if len(sig) > 90:
            sig = sig[:87] + "..."
        print(f"  #{c['rank']}  [{c['severity']:5s}] "
              f"score={c['total_score']:.2f}  "
              f"count={c['session_count']}")
        print(f"        {sig}")
        print()
    return 0


def cmd_init_db(args) -> int:
    storage.init_db()
    print(f"Initialized database at {storage.DEFAULT_DB_PATH}")
    return 0

w

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze",
                               help="Pull logs and run analysis")
    p_analyze.add_argument("--log-group", required=True)
    p_analyze.add_argument("--window", type=int, default=15,
                           help="Window length in minutes (default 15)")
    p_analyze.add_argument("--ago", type=int, default=0,
                           help="Shift the window N minutes into the past")
    p_analyze.add_argument("--fake", action="store_true",
                           help="Use the fake CloudWatch fixture")
    p_analyze.add_argument("--profile", default="tracer",
                           help="AWS profile to use (when not --fake)")
    p_analyze.add_argument("--json", action="store_true",
                           help="Emit JSON instead of formatted text")
    p_analyze.set_defaults(func=cmd_analyze)

    p_history = sub.add_parser("history",
                               help="List recent analyze sessions")
    p_history.add_argument("--log-group", default=None)
    p_history.add_argument("--limit", type=int, default=20)
    p_history.set_defaults(func=cmd_history)

    p_inspect = sub.add_parser("inspect",
                               help="Show ranked candidates for a session")
    p_inspect.add_argument("session_id")
    p_inspect.set_defaults(func=cmd_inspect)

    p_init = sub.add_parser("init-db", help="Initialize the database")
    p_init.set_defaults(func=cmd_init_db)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
