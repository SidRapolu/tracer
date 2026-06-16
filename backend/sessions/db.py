from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    # Commit to psycopg connection on clean exit and rollback on exception.
    url = os.environ.get("TRACER_DATABASE_URL")
    if not url:
        raise RuntimeError("TRACER_DATABASE_URL is not set. See .env.example.")

    conn = psycopg.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

-- One "analyze" invocation against one service and time window.
CREATE TABLE IF NOT EXISTS sessions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service       TEXT        NOT NULL,
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'running',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_service ON sessions (service);

-- A normalized log-line template, keyed per service. 
CREATE TABLE IF NOT EXISTS signatures (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service       TEXT        NOT NULL,
    fingerprint   TEXT        NOT NULL,
    template      TEXT        NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (service, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_signatures_service ON signatures (service);

-- Per-session occurrence counts. 
CREATE TABLE IF NOT EXISTS signature_occurrences (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signature_id  BIGINT      NOT NULL REFERENCES signatures (id) ON DELETE CASCADE,
    session_id    BIGINT      NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    count         INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (signature_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_occ_signature ON signature_occurrences (signature_id);
CREATE INDEX IF NOT EXISTS idx_occ_session   ON signature_occurrences (session_id);

-- One ranked candidate's persisted result.
CREATE TABLE IF NOT EXISTS analyses (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id      BIGINT      NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    signature_id    BIGINT      REFERENCES signatures (id) ON DELETE SET NULL,
    rank            INTEGER     NOT NULL,
    composite_score DOUBLE PRECISION NOT NULL,
    verdict_line    TEXT,
    hypothesis      TEXT,
    next_step       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analyses_session ON analyses (session_id);
"""


def init_schema() -> None:
    # Apply the schema idempotently. 
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_DDL)
