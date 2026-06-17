from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

from ranking.baseline import baseline_stats, nearest_baseline_distance
from ranking.score import (
    Candidate,
    composite_score,
    novelty_score,
    rate_change_score,
    severity_score,
)
from sessions.models import record_analysis

# Signatures present in this session: (id, template, incident_count, embedding).
def _incident_signatures(
    conn: psycopg.Connection,
    session_id: int,
) -> list[tuple[int, str, int, list[float] | None]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.template, o.count, s.embedding
            FROM signature_occurrences o
            JOIN signatures s ON s.id = o.signature_id
            WHERE o.session_id = %s
            """,
            (session_id,),
        )
        return cur.fetchall()

# Score and rank the incident session's signatures; persist to analyses.
def rank_session(
    conn: psycopg.Connection,
    service: str,
    session_id: int,
) -> list[Candidate]:
    register_vector(conn)

    rows = _incident_signatures(conn, session_id)
    signature_ids = [r[0] for r in rows]
    stats = baseline_stats(conn, service, signature_ids)

    candidates: list[Candidate] = []
    for sig_id, template, incident_count, embedding in rows:
        stat = stats[sig_id]
        has_history = stat.baseline_sessions > 0

        nearest = None
        if not has_history and embedding is not None:
            nearest = nearest_baseline_distance(conn, service, embedding, sig_id)

        novelty = novelty_score(has_history, nearest)
        rate = rate_change_score(incident_count, stat.avg_count)
        severity = severity_score(template)
        composite = composite_score(novelty, rate, severity)

        candidates.append(
            Candidate(
                signature_id=sig_id,
                template=template,
                incident_count=incident_count,
                novelty=round(novelty, 4),
                rate_change=round(rate, 4),
                severity=round(severity, 4),
                composite=round(composite, 4),
            )
        )

    candidates.sort(key=lambda c: c.composite, reverse=True)

    for rank, c in enumerate(candidates, start=1):
        record_analysis(
            conn,
            session_id=session_id,
            rank=rank,
            composite_score=c.composite,
            signature_id=c.signature_id,
        )

    return candidates
