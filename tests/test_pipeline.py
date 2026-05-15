"""
End-to-end pipeline test using the fake CloudWatch fixture.

Verifies the core promise of the tool: after a baseline-establishing run
on quiet history, a follow-up run that includes the incident should
surface the planted NullPointerException as a top-ranked candidate.
"""

import tempfile
import time
from pathlib import Path

from tracer import baseline, candidates, ingest, signatures, storage
from tracer.fake_aws import FakeLogsClient


FIXTURE = (Path(__file__).resolve().parents[1]
           / "fixtures" / "sample_logs.json")
LOG_GROUP = "/aws/lambda/offers-service"


def _run_analyze(db_path: Path, client: FakeLogsClient,
                 window_min: int, ago_min: int) -> tuple[str, list[dict]]:
    storage.DEFAULT_DB_PATH = db_path  # type: ignore[attr-defined]
    storage.init_db(db_path)

    start_ms, end_ms = ingest.window_from_args(window_min, ago_min)
    session_id, events = ingest.fetch_logs(client, LOG_GROUP, start_ms, end_ms)

    with storage.connect(db_path) as conn:
        storage.create_session(conn, session_id, LOG_GROUP, start_ms, end_ms)
        storage.insert_events(conn, session_id, LOG_GROUP, events)
        storage.update_session_count(conn, session_id, len(events))

        stored_events = storage.events_for_session(conn, session_id)
        sigged = signatures.signaturize(stored_events)
        session_counts = baseline.record_session_signatures(
            conn, session_id, LOG_GROUP, sigged
        )
        ranked = candidates.score_session(
            conn, session_id, LOG_GROUP, session_counts
        )
    return session_id, ranked


def test_incident_surfaces_after_baseline_established():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        # Anchor the fake client to a fixed "now" so windowing is stable
        # across test runs.
        anchor = int(time.time() * 1000)
        client = FakeLogsClient(FIXTURE, now_ms=anchor)

        # First session: pull historical-only window (ending 30 min ago,
        # 60 min wide). This is mostly normal traffic — no incident.
        _, first_ranked = _run_analyze(db_path, client,
                                        window_min=60, ago_min=30)

        # Second session: pull the current 15-min window. Incident lives
        # in the last 10 min of the fixture, so this catches it.
        _, second_ranked = _run_analyze(db_path, client,
                                         window_min=15, ago_min=0)

        # The incident signatures should appear at the top of the second
        # session's candidate list.
        top_3_sigs = [c["signature_text"] for c in second_ranked[:3]]
        joined = " | ".join(top_3_sigs)

        # The planted incident messages all mention NullPointerException
        # or null_segment. At least one should be in the top 3.
        assert any(
            "NullPointerException" in s or "null_segment" in s
            for s in top_3_sigs
        ), f"Incident did not surface in top 3. Top 3 were: {joined}"


def test_known_noise_does_not_top_rank():
    """The recurring health-check INFO line should never be a top candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        anchor = int(time.time() * 1000)
        client = FakeLogsClient(FIXTURE, now_ms=anchor)

        # Establish baseline first
        _run_analyze(db_path, client, window_min=60, ago_min=30)
        # Then analyze the incident window
        _, ranked = _run_analyze(db_path, client, window_min=15, ago_min=0)

        top_5 = [c["signature_text"] for c in ranked[:5]]
        assert not any("Health check OK" in s for s in top_5), (
            f"Health check appeared in top 5: {top_5}"
        )
