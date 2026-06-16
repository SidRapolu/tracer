"""
tests/test_ingestion.py

Ingestion tests run offline with no AWS and no database: a fake boto3 client
exercises CloudWatch pagination and epoch-ms handling, and the fixture source
covers window filtering. The Postgres test is marked `db` and skips when
TRACER_DATABASE_URL points at no reachable database.

    pytest                       # everything (Postgres test runs if DB is up)
    pytest -m "not db"           # offline ingestion tests only
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.cloudwatch import CloudWatchLogSource, _from_epoch_ms, _to_epoch_ms
from ingestion.fixtures import FixtureLogSource
from ingestion.source import LogEvent

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sample_logs.json"
UTC = timezone.utc


# --- ingestion (offline) ---------------------------------------------------

def test_epoch_ms_roundtrip():
    dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    assert _from_epoch_ms(_to_epoch_ms(dt)) == dt


def test_epoch_ms_is_milliseconds():
    # Seconds would be ~1.78e9; milliseconds ~1.78e12.
    assert _to_epoch_ms(datetime(2026, 5, 1, tzinfo=UTC)) > 1_000_000_000_000


class _FakeLogsClient:
    """Returns canned pages and asserts nextToken is followed."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def filter_log_events(self, **kwargs):
        page = self._pages[self.calls]
        self.calls += 1
        if self.calls == 1:
            assert "nextToken" not in kwargs
        else:
            assert kwargs.get("nextToken")
        return page


def test_cloudwatch_follows_pagination():
    pages = [
        {
            "events": [{"timestamp": _to_epoch_ms(datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)),
                        "message": "first", "logStreamName": "s1"}],
            "nextToken": "tok-2",
        },
        {
            "events": [{"timestamp": _to_epoch_ms(datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC)),
                        "message": "second", "logStreamName": "s1"}],
        },
    ]
    client = _FakeLogsClient(pages)
    events = CloudWatchLogSource(client).fetch(
        "/aws/lambda/checkout",
        datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
        datetime(2026, 5, 1, 13, 0, 0, tzinfo=UTC),
    )
    assert client.calls == 2
    assert [e.message for e in events] == ["first", "second"]
    assert all(isinstance(e, LogEvent) for e in events)


def test_fixture_filters_to_incident_window():
    events = FixtureLogSource(FIXTURE).fetch(
        "/aws/lambda/checkout",
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 5, 1, 13, 0, 0, tzinfo=UTC),
    )
    assert events
    assert any("NullPointerException" in e.message for e in events)


def test_fixture_excludes_baseline_window():
    events = FixtureLogSource(FIXTURE).fetch(
        "/aws/lambda/checkout",
        datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
    )
    assert events
    assert not any("NullPointerException" in e.message for e in events)


def test_fixture_events_sorted():
    events = FixtureLogSource(FIXTURE).fetch(
        "/aws/lambda/checkout",
        datetime(2026, 5, 1, tzinfo=UTC),
        datetime(2026, 5, 2, tzinfo=UTC),
    )
    assert [e.timestamp for e in events] == sorted(e.timestamp for e in events)


# --- Postgres (live) -------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from sessions.db import connect
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.db
@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_session_lifecycle():
    from sessions.db import connect, init_schema
    from sessions.models import (
        create_session,
        get_analyses_for_session,
        get_session,
        record_analysis,
        set_session_status,
    )

    init_schema()
    with connect() as conn:
        sid = create_session(
            conn,
            "/aws/lambda/checkout",
            datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 1, 13, 0, 0, tzinfo=UTC),
        )
        record_analysis(conn, sid, rank=1, composite_score=0.91)
        record_analysis(conn, sid, rank=2, composite_score=0.55)
        set_session_status(conn, sid, "complete")

        assert get_session(conn, sid).status == "complete"
        rows = get_analyses_for_session(conn, sid)
        assert [r["rank"] for r in rows] == [1, 2]
        assert rows[0]["verdict_line"] is None
