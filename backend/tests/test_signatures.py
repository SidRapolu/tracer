"""
tests/test_signatures.py

Normalizer tests run offline. The store test is marked `db`: it runs against
live Postgres but injects a fake embedder, so it exercises the lazy-embed
control flow (hit vs miss) and occurrence counting deterministically without
spending Titan calls. The real Titan path is covered by build_embedder + the
manual invoke check; here the store logic is what's under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingestion.source import LogEvent
from signatures.normalize import fingerprint, normalize

UTC = timezone.utc


# --- normalizer (offline) --------------------------------------------------

def test_variable_request_lines_collapse():
    a = normalize("INFO request id=a91 completed in 42ms")
    b = normalize("INFO request id=b12 completed in 38ms")
    assert a == b
    assert fingerprint(a) == fingerprint(b)


def test_different_messages_differ():
    a = normalize("INFO health check ok")
    b = normalize("ERROR NullPointerException at PaymentProcessor.charge line 88")
    assert fingerprint(a) != fingerprint(b)


def test_line_number_collapses():
    a = normalize("ERROR NPE at X line 88")
    b = normalize("ERROR NPE at X line 102")
    assert a == b


def test_uuid_and_hex_collapse():
    t = normalize("trace id=550e8400-e29b-41d4-a716-446655440000 at 0xdeadbeef")
    assert "<UUID>" in t and "<HEX>" in t


# --- store (live Postgres, fake embedder) ----------------------------------

class _FakeEmbedder:
    """Returns a fixed 1024-d vector and counts calls, so the test can assert
    embedding happened only on novel signatures."""

    def __init__(self):
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [0.0] * 1024


def _db_reachable() -> bool:
    try:
        from sessions.db import connect
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def _events(messages, group="/aws/lambda/checkout"):
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    return [
        LogEvent(timestamp=base, message=m, log_group=group, log_stream="s1")
        for m in messages
    ]


@pytest.mark.db
@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_lazy_embed_only_on_novel():
    from sessions.db import connect, init_schema
    from sessions.models import create_session
    from signatures.store import ingest_signatures

    init_schema()
    embedder = _FakeEmbedder()
    service = "/aws/lambda/test-lazy-embed"

    with connect() as conn:
        # Clean slate for this service so the test is repeatable.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM signatures WHERE service = %s", (service,))

        s1 = create_session(conn, service, datetime.now(UTC), datetime.now(UTC))
        first = ingest_signatures(
            conn, embedder, service, s1,
            _events([
                "INFO request id=a91 completed in 42ms",
                "INFO request id=b12 completed in 38ms",   # same signature
                "ERROR NPE at X line 88",
            ], service),
        )
        # Two distinct signatures, both novel -> two embed calls.
        assert first == {"distinct": 2, "novel": 2}
        assert embedder.calls == 2

        # Second run, same signatures -> exact-match hits, no new embed calls.
        s2 = create_session(conn, service, datetime.now(UTC), datetime.now(UTC))
        second = ingest_signatures(
            conn, embedder, service, s2,
            _events(["INFO request id=c01 completed in 10ms"], service),
        )
        assert second == {"distinct": 1, "novel": 0}
        assert embedder.calls == 2  # unchanged — lazy embed held
