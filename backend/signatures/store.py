from __future__ import annotations

from collections import Counter
from typing import Optional

import psycopg
from pgvector.psycopg import register_vector

from ingestion.source import LogEvent
from ranking.score import severity_score
from signatures.embed import TitanEmbedder
from signatures.normalize import fingerprint, normalize

# Signatures scoring at or above this severity are errors, not normal traffic.
# When learning a baseline, they're dropped so a burst that lands in a sampled
# "normal" window can't teach the baseline that errors are normal. WARN and
# below (e.g. slow-query warnings) stay — they're legitimate background noise.
_BASELINE_SEVERITY_CUTOFF = 0.85


def _get_signature_id(
    conn: psycopg.Connection,
    service: str,
    fp: str,
) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM signatures WHERE service = %s AND fingerprint = %s",
            (service, fp),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _insert_signature(
    conn: psycopg.Connection,
    service: str,
    fp: str,
    template: str,
    embedding: list[float],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signatures (service, fingerprint, template, embedding)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (service, fp, template, embedding),
        )
        return cur.fetchone()[0]


import json

# Max distinct occurrences to sample raw context for, and how many lines of
# surrounding context (before/after) to keep around each. Small on purpose:
# the verdict layer needs a representative taste, not the whole window.
_MAX_SAMPLES = 3
_CONTEXT_RADIUS = 2


def _record_occurrence(
    conn: psycopg.Connection,
    signature_id: int,
    session_id: int,
    count: int,
    sample_lines: list[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signature_occurrences (signature_id, session_id, count, sample_lines)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (signature_id, session_id)
            DO UPDATE SET count = signature_occurrences.count + EXCLUDED.count
            """,
            (signature_id, session_id, count, json.dumps(sample_lines)),
        )

# Process a window of events into signatures + occurrences for one session.
# Returns a small summary: distinct signatures stored, how many were novel
# (and therefore embedded), and how many were skipped as too severe.
def ingest_signatures(
    conn: psycopg.Connection,
    embedder: TitanEmbedder,
    service: str,
    session_id: int,
    events: list[LogEvent],
    skip_high_severity: bool = False,
) -> dict[str, int]:
    register_vector(conn)

    counts: Counter[str] = Counter()
    templates: dict[str, str] = {}
    samples: dict[str, list[str]] = {}
    for i, event in enumerate(events):
        template = normalize(event.message)
        fp = fingerprint(template)
        counts[fp] += 1
        templates[fp] = template
        # Keep a few windowed samples: the matching line plus nearby lines, so
        # the verdict layer can see what surrounded each occurrence.
        if len(samples.get(fp, [])) < _MAX_SAMPLES:
            lo = max(0, i - _CONTEXT_RADIUS)
            hi = min(len(events), i + _CONTEXT_RADIUS + 1)
            window = [e.message for e in events[lo:hi]]
            samples.setdefault(fp, []).append(" | ".join(window))

    novel = 0
    skipped = 0
    for fp, count in counts.items():
        if skip_high_severity and severity_score(templates[fp]) >= _BASELINE_SEVERITY_CUTOFF:
            skipped += 1
            continue
        signature_id = _get_signature_id(conn, service, fp)
        if signature_id is None:
            embedding = embedder.embed(templates[fp])
            signature_id = _insert_signature(conn, service, fp, templates[fp], embedding)
            novel += 1
        _record_occurrence(conn, signature_id, session_id, count, samples.get(fp, []))

    return {"distinct": len(counts) - skipped, "novel": novel, "skipped": skipped}
