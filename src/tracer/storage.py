"""
SQLite storage for tracer.

Schema is designed to support Days 1–3 cleanly:
  - log_events:        raw events pulled in each analyze session (Day 1)
  - analysis_sessions: one row per "analyze" click (Day 1)
  - signatures:        normalized log-line shapes seen across all time (Day 2)
  - signature_occurrences: per-session counts of each signature (Day 2 + 3)
  - candidates:        ranked candidates produced by an analyze session (Day 3)

The signatures + signature_occurrences split is intentional: signatures is the
long-lived "what does normal look like for this log group" memory, and
signature_occurrences is the audit trail of what was seen in each session.
That separation is what makes baseline learning work cleanly.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path("data/tracer.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_sessions (
    id              TEXT PRIMARY KEY,
    log_group       TEXT NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms   INTEGER NOT NULL,
    created_at_ms   INTEGER NOT NULL,
    event_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS log_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    log_group           TEXT NOT NULL,
    log_stream          TEXT NOT NULL,
    timestamp_ms        INTEGER NOT NULL,
    message             TEXT NOT NULL,
    signature_id        INTEGER,
    FOREIGN KEY (session_id) REFERENCES analysis_sessions(id),
    FOREIGN KEY (signature_id) REFERENCES signatures(id)
);

CREATE INDEX IF NOT EXISTS idx_events_session
    ON log_events(session_id);

CREATE INDEX IF NOT EXISTS idx_events_group_ts
    ON log_events(log_group, timestamp_ms);

CREATE TABLE IF NOT EXISTS signatures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    log_group       TEXT NOT NULL,
    signature_text  TEXT NOT NULL,
    severity        TEXT NOT NULL,
    first_seen_ms   INTEGER NOT NULL,
    last_seen_ms    INTEGER NOT NULL,
    total_count     INTEGER NOT NULL DEFAULT 0,
    session_count   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (log_group, signature_text)
);

CREATE INDEX IF NOT EXISTS idx_signatures_group
    ON signatures(log_group);

-- Per-session count of each signature. This is what lets us reason about
-- rate changes (this session's count vs historical average per session).
CREATE TABLE IF NOT EXISTS signature_occurrences (
    signature_id    INTEGER NOT NULL,
    session_id      TEXT NOT NULL,
    count           INTEGER NOT NULL,
    PRIMARY KEY (signature_id, session_id),
    FOREIGN KEY (signature_id) REFERENCES signatures(id),
    FOREIGN KEY (session_id) REFERENCES analysis_sessions(id)
);

-- Ranked candidates produced by Day 3 scoring. was_actual_cause is the
-- feedback hook for Day 6: when the user marks a candidate as the real
-- root cause, we record it here for future weighting.
CREATE TABLE IF NOT EXISTS candidates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    signature_id     INTEGER NOT NULL,
    rank             INTEGER NOT NULL,
    total_score      REAL NOT NULL,
    novelty_score    REAL NOT NULL,
    rate_score       REAL NOT NULL,
    severity_score   REAL NOT NULL,
    session_count    INTEGER NOT NULL,
    was_actual_cause INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES analysis_sessions(id),
    FOREIGN KEY (signature_id) REFERENCES signatures(id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_session
    ON candidates(session_id, rank);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


@contextmanager
def connect(db_path: Path = DEFAULT_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)

def create_session(conn, session_id: str, log_group: str,
                   window_start_ms: int, window_end_ms: int) -> None:
    conn.execute(
        """
        INSERT INTO analysis_sessions
            (id, log_group, window_start_ms, window_end_ms, created_at_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, log_group, window_start_ms, window_end_ms, now_ms()),
    )


def update_session_count(conn, session_id: str, count: int) -> None:
    conn.execute(
        "UPDATE analysis_sessions SET event_count = ? WHERE id = ?",
        (count, session_id),
    )


def list_sessions(conn, log_group: str | None = None, limit: int = 20):
    if log_group:
        rows = conn.execute(
            "SELECT * FROM analysis_sessions WHERE log_group = ? "
            "ORDER BY created_at_ms DESC LIMIT ?",
            (log_group, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analysis_sessions ORDER BY created_at_ms DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]

def insert_events(conn, session_id: str, log_group: str,
                  events: Iterable[dict]) -> int:
    """
    Insert raw events for a session. Returns the count inserted.
    signature_id is left NULL here; it gets filled in by the signatures pass.
    """
    rows = [
        (session_id, log_group, e["logStreamName"],
         e["timestamp"], e["message"])
        for e in events
    ]
    conn.executemany(
        """
        INSERT INTO log_events
            (session_id, log_group, log_stream, timestamp_ms, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def events_for_session(conn, session_id: str):
    rows = conn.execute(
        "SELECT * FROM log_events WHERE session_id = ? ORDER BY timestamp_ms",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_event_signature(conn, event_id: int, signature_id: int) -> None:
    conn.execute(
        "UPDATE log_events SET signature_id = ? WHERE id = ?",
        (signature_id, event_id),
    )

def upsert_signature(conn, log_group: str, signature_text: str,
                     severity: str, observed_at_ms: int) -> int:
    """
    Returns the signature id. Creates it if new; otherwise updates last_seen.
    """
    row = conn.execute(
        "SELECT id, first_seen_ms FROM signatures "
        "WHERE log_group = ? AND signature_text = ?",
        (log_group, signature_text),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            """
            INSERT INTO signatures
                (log_group, signature_text, severity,
                 first_seen_ms, last_seen_ms, total_count, session_count)
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (log_group, signature_text, severity,
             observed_at_ms, observed_at_ms),
        )
        return cur.lastrowid
    conn.execute(
        "UPDATE signatures SET last_seen_ms = ? WHERE id = ?",
        (observed_at_ms, row["id"]),
    )
    return row["id"]


def record_signature_occurrence(conn, signature_id: int,
                                session_id: str, count: int) -> None:
    """
    Record that this signature occurred `count` times in this session,
    and update the signature's running totals.
    """
    conn.execute(
        """
        INSERT INTO signature_occurrences (signature_id, session_id, count)
        VALUES (?, ?, ?)
        ON CONFLICT(signature_id, session_id)
        DO UPDATE SET count = excluded.count
        """,
        (signature_id, session_id, count),
    )
    # Also bump totals on the signature itself.
    conn.execute(
        """
        UPDATE signatures
        SET total_count = (
                SELECT COALESCE(SUM(count), 0)
                FROM signature_occurrences
                WHERE signature_id = ?
            ),
            session_count = (
                SELECT COUNT(*)
                FROM signature_occurrences
                WHERE signature_id = ?
            )
        WHERE id = ?
        """,
        (signature_id, signature_id, signature_id),
    )


def get_signature(conn, signature_id: int):
    row = conn.execute(
        "SELECT * FROM signatures WHERE id = ?", (signature_id,)
    ).fetchone()
    return dict(row) if row else None


def historical_session_count_for_group(conn, log_group: str,
                                       excluding_session: str) -> int:
    """
    How many prior analyze sessions exist for this log group, excluding
    the current one. Used as the denominator for "average occurrences per
    session" baselines.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM analysis_sessions "
        "WHERE log_group = ? AND id != ?",
        (log_group, excluding_session),
    ).fetchone()
    return row["c"] if row else 0


def historical_signature_stats(conn, signature_id: int,
                               excluding_session: str) -> dict:
    """
    Return total occurrences and number of prior sessions this signature
    has appeared in, excluding the current session. This is the baseline.
    """
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(count), 0) AS total,
            COUNT(*) AS sessions_present
        FROM signature_occurrences
        WHERE signature_id = ? AND session_id != ?
        """,
        (signature_id, excluding_session),
    ).fetchone()
    return {
        "total": row["total"] if row else 0,
        "sessions_present": row["sessions_present"] if row else 0,
    }

def insert_candidates(conn, session_id: str, ranked: list[dict]) -> None:
    conn.execute("DELETE FROM candidates WHERE session_id = ?", (session_id,))
    rows = [
        (session_id, c["signature_id"], c["rank"], c["total_score"],
         c["novelty_score"], c["rate_score"], c["severity_score"],
         c["session_count"])
        for c in ranked
    ]
    conn.executemany(
        """
        INSERT INTO candidates
            (session_id, signature_id, rank, total_score,
             novelty_score, rate_score, severity_score, session_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def candidates_for_session(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.*, s.signature_text, s.severity, s.total_count,
               s.session_count AS sig_session_count, s.first_seen_ms
        FROM candidates c
        JOIN signatures s ON s.id = c.signature_id
        WHERE c.session_id = ?
        ORDER BY c.rank
        """,
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_candidate_as_cause(conn, session_id: str, signature_id: int) -> None:
    """Day 6 hook: user confirms which candidate was the real cause."""
    conn.execute(
        "UPDATE candidates SET was_actual_cause = 0 WHERE session_id = ?",
        (session_id,),
    )
    conn.execute(
        "UPDATE candidates SET was_actual_cause = 1 "
        "WHERE session_id = ? AND signature_id = ?",
        (session_id, signature_id),
    )
