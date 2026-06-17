from __future__ import annotations

from dataclasses import dataclass

import psycopg

#A signature absent from every baseline session has no
#history — that is the exact-match novelty signal.

# Historical view of one signature across baseline sessions.
@dataclass
class BaselineStat:
    signature_id: int
    baseline_sessions: int      # how many baseline sessions contained it
    avg_count: float            # mean occurrences per baseline session (0 if never seen)

# Total number of baseline sessions for a service.
def baseline_session_count(conn: psycopg.Connection, service: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM sessions WHERE service = %s AND kind = 'baseline'",
            (service,),
        )
        return cur.fetchone()[0]

# For each signature id, its occurrence stats across baseline sessions.
# Signatures never seen in a baseline session get avg_count 0.
def baseline_stats(
    conn: psycopg.Connection,
    service: str,
    signature_ids: list[int],
) -> dict[int, BaselineStat]:
    if not signature_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.signature_id,
                   count(DISTINCT o.session_id) AS sessions,
                   sum(o.count)                 AS total
            FROM signature_occurrences o
            JOIN sessions s ON s.id = o.session_id
            WHERE s.service = %s
              AND s.kind = 'baseline'
              AND o.signature_id = ANY(%s)
            GROUP BY o.signature_id
            """,
            (service, signature_ids),
        )
        rows = {sig: (sessions, total) for sig, sessions, total in cur.fetchall()}

    n_baseline = baseline_session_count(conn, service)
    stats: dict[int, BaselineStat] = {}
    for sig in signature_ids:
        sessions, total = rows.get(sig, (0, 0))
        avg = (total / n_baseline) if n_baseline else 0.0
        stats[sig] = BaselineStat(sig, sessions, avg)
    return stats

# Cosine distance from `embedding` to the nearest *other* baseline-seen
# signature for the service. Returns None if there is no neighbor to compare
# against. pgvector's <=> operator is cosine distance (0 = identical).
def nearest_baseline_distance(
    conn: psycopg.Connection,
    service: str,
    embedding: list[float],
    exclude_signature_id: int,
) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT min(sig.embedding <=> %s::vector)
            FROM signatures sig
            WHERE sig.service = %s
              AND sig.id <> %s
              AND sig.embedding IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM signature_occurrences o
                  JOIN sessions s ON s.id = o.session_id
                  WHERE o.signature_id = sig.id AND s.kind = 'baseline'
              )
            """,
            (embedding, service, exclude_signature_id),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else None
