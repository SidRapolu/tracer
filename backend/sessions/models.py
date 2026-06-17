# The smallest set of functions the pipeline needs to open a session, set its status, and persist ranked analyses.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg


@dataclass
class Session:
    id: int
    service: str
    kind: str
    window_start: datetime
    window_end: datetime
    status: str
    created_at: datetime


def create_session(
    conn: psycopg.Connection,
    service: str,
    window_start: datetime,
    window_end: datetime,
    kind: str = "incident",
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (service, kind, window_start, window_end, status)
            VALUES (%s, %s, %s, %s, 'running')
            RETURNING id
            """,
            (service, kind, window_start, window_end),
        )
        return cur.fetchone()[0]


def set_session_status(
    conn: psycopg.Connection,
    session_id: int,
    status: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET status = %s WHERE id = %s",
            (status, session_id),
        )


def get_session(conn: psycopg.Connection, session_id: int) -> Optional[Session]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, service, kind, window_start, window_end, status, created_at
            FROM sessions WHERE id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
    return Session(*row) if row else None


def record_analysis(
    conn: psycopg.Connection,
    session_id: int,
    rank: int,
    composite_score: float,
    signature_id: Optional[int] = None,
    verdict_line: Optional[str] = None,
    hypothesis: Optional[str] = None,
    next_step: Optional[str] = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analyses
                (session_id, signature_id, rank, composite_score,
                 verdict_line, hypothesis, next_step)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                session_id,
                signature_id,
                rank,
                composite_score,
                verdict_line,
                hypothesis,
                next_step,
            ),
        )
        return cur.fetchone()[0]


def get_analyses_for_session(
    conn: psycopg.Connection,
    session_id: int,
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, session_id, signature_id, rank, composite_score,
                   verdict_line, hypothesis, next_step, created_at
            FROM analyses
            WHERE session_id = %s
            ORDER BY rank ASC
            """,
            (session_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
