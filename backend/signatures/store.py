from __future__ import annotations

from collections import Counter
from typing import Optional

import psycopg
from pgvector.psycopg import register_vector

from ingestion.source import LogEvent
from signatures.embed import TitanEmbedder
from signatures.normalize import fingerprint, normalize


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


def _record_occurrence(
    conn: psycopg.Connection,
    signature_id: int,
    session_id: int,
    count: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signature_occurrences (signature_id, session_id, count)
            VALUES (%s, %s, %s)
            ON CONFLICT (signature_id, session_id)
            DO UPDATE SET count = signature_occurrences.count + EXCLUDED.count
            """,
            (signature_id, session_id, count),
        )


def ingest_signatures(
    conn: psycopg.Connection,
    embedder: TitanEmbedder,
    service: str,
    session_id: int,
    events: list[LogEvent],
) -> dict[str, int]:
    # Process a window of events into signatures + occurrences for one session.
    register_vector(conn)

    counts: Counter[str] = Counter()
    templates: dict[str, str] = {}
    for event in events:
        template = normalize(event.message)
        fp = fingerprint(template)
        counts[fp] += 1
        templates[fp] = template

    novel = 0
    for fp, count in counts.items():
        signature_id = _get_signature_id(conn, service, fp)
        if signature_id is None:
            embedding = embedder.embed(templates[fp])
            signature_id = _insert_signature(conn, service, fp, templates[fp], embedding)
            novel += 1
        _record_occurrence(conn, signature_id, session_id, count)

    return {"distinct": len(counts), "novel": novel}
