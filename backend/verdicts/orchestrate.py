from __future__ import annotations

import psycopg

from verdicts.generate import VerdictGenerator


# Pull the top-N ranked candidates for a session, joined with their template,
# incident count, scores, and sampled raw lines — everything the model needs.
def _top_candidates(
    conn: psycopg.Connection,
    session_id: int,
    limit: int,
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.rank, a.composite_score,
                   s.template, o.count, o.sample_lines
            FROM analyses a
            JOIN signatures s ON s.id = a.signature_id
            JOIN signature_occurrences o
              ON o.signature_id = a.signature_id AND o.session_id = a.session_id
            WHERE a.session_id = %s
            ORDER BY a.rank ASC
            LIMIT %s
            """,
            (session_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# Persist a generated verdict back onto its analyses row.
def _save_verdict(conn: psycopg.Connection, analysis_id: int, verdict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analyses
            SET verdict_line = %s, hypothesis = %s, next_step = %s
            WHERE id = %s
            """,
            (verdict.verdict_line, verdict.hypothesis, verdict.next_step, analysis_id),
        )


# Generate and persist verdicts for the top-N candidates of a session.
def generate_verdicts(
    conn: psycopg.Connection,
    generator: VerdictGenerator,
    session_id: int,
    top_n: int = 3,
) -> list[dict]:
    rows = _top_candidates(conn, session_id, top_n)
    results = []
    for row in rows:
        candidate = {
            "template": row["template"],
            "incident_count": row["count"],
            "composite": row["composite_score"],
            "sample_lines": row["sample_lines"],
        }
        verdict = generator.generate(candidate)
        _save_verdict(conn, row["id"], verdict)
        results.append(
            {
                "rank": row["rank"],
                "template": row["template"],
                "verdict_line": verdict.verdict_line,
                "hypothesis": verdict.hypothesis,
                "next_step": verdict.next_step,
            }
        )
    return results
