from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from config import build_embedder, load_config
from ranking.rank import rank_session
from sessions.db import connect, init_schema
from sessions.models import create_session, set_session_status
from signatures.normalize import fingerprint, normalize
from signatures.store import ingest_signatures
from ingestion.source import LogEvent

UTC = timezone.utc
CASES_DIR = Path(__file__).resolve().parent / "cases"


# One labeled case and the rank its true root cause achieved.
@dataclass
class CaseResult:
    name: str
    root_cause_template: str
    rank: int | None          # 1-based rank of the true cause, or None if absent
    top_candidate: str


# Build synthetic events at sequential timestamps within a window.
def _events(messages: list[dict], service: str, base: datetime) -> list[LogEvent]:
    return [
        LogEvent(
            timestamp=base + timedelta(seconds=i),
            message=m["message"],
            log_group=service,
            log_stream="eval",
        )
        for i, m in enumerate(messages)
    ]


# Run one case end to end against live Postgres + Titan, return where the
# labeled root cause landed in the ranking.
def run_case(conn, embedder, case: dict) -> CaseResult:
    # Unique service per run so repeated eval runs don't accumulate baselines.
    service = f"{case['service']}-{datetime.now(UTC).timestamp()}"

    base = datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC)
    bsid = create_session(conn, service, base, base + timedelta(minutes=10), kind="baseline")
    ingest_signatures(conn, embedder, service, bsid, _events(case["baseline"], service, base))
    set_session_status(conn, bsid, "complete")

    inc_base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    isid = create_session(conn, service, inc_base, inc_base + timedelta(minutes=10), kind="incident")
    ingest_signatures(conn, embedder, service, isid, _events(case["incident"], service, inc_base))
    candidates = rank_session(conn, service, isid)
    set_session_status(conn, isid, "complete")

    # Find the labeled root cause by its normalized template fingerprint.
    target_fp = fingerprint(normalize(case["root_cause_template"]))
    rank = None
    for i, c in enumerate(candidates, start=1):
        if fingerprint(c.template) == target_fp:
            rank = i
            break

    top = candidates[0].template if candidates else "(none)"
    return CaseResult(case["name"], case["root_cause_template"], rank, top)


def main() -> int:
    config = load_config()
    embedder = build_embedder(config)

    cases = sorted(CASES_DIR.glob("*.json"))
    if not cases:
        print("no eval cases found")
        return 1

    init_schema()
    results: list[CaseResult] = []
    with connect() as conn:
        for path in cases:
            case = json.loads(path.read_text())
            results.append(run_case(conn, embedder, case))

    top1 = sum(1 for r in results if r.rank == 1)
    top3 = sum(1 for r in results if r.rank is not None and r.rank <= 3)
    n = len(results)

    print("eval results")
    print("-" * 60)
    for r in results:
        status = f"rank {r.rank}" if r.rank else "MISSING"
        print(f"  {r.name:24s} {status}")
        if r.rank != 1:
            print(f"      expected: {r.root_cause_template}")
            print(f"      top was:  {r.top_candidate}")
    print("-" * 60)
    print(f"top-1 accuracy: {top1}/{n} = {top1 / n:.0%}")
    print(f"top-3 accuracy: {top3}/{n} = {top3 / n:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())