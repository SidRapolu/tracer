from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import boto3

from api.groups import list_groups_by_error_volume
from config import build_embedder, build_verdict_generator, load_config
from ingestion.cloudwatch import CloudWatchLogSource
from ranking.baseline import baseline_session_count, prune_baseline_sessions
from ranking.rank import rank_session
from sessions.db import connect, init_schema
from sessions.models import create_session, set_session_status
from signatures.store import ingest_signatures
from verdicts.orchestrate import generate_verdict_for_rank

# Incident window: how far back "now" the incident slice runs.
INCIDENT_MINUTES = 5
# Learn window: how much recent traffic one learn call samples as normal.
LEARN_MINUTES = 15

app = FastAPI(title="Tracer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache incident analyses by group so re-clicking doesn't re-spend.
_analysis_cache: dict[str, dict] = {}


def _region() -> str:
    return load_config().aws_region


def _logs_client():
    return boto3.client("logs", region_name=_region())


class GroupRequest(BaseModel):
    log_group: str
    minutes: int = LEARN_MINUTES


class AnalyzeRequest(BaseModel):
    log_group: str
    refresh: bool = False


class VerdictRequest(BaseModel):
    session_id: int
    rank: int


@app.get("/groups")
def groups():
    client = _logs_client()
    return {"groups": list_groups_by_error_volume(client)}


# Sample a recent window of a service's logs and merge the non-severe
# signatures into its stored baseline, then prune to the most recent
# baseline sessions. Currently triggered manually for development, 
# will run on schedule in production.
@app.post("/learn")
def learn(req: GroupRequest):
    config = load_config()
    source = CloudWatchLogSource(_logs_client())
    embedder = build_embedder(config)

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=req.minutes)

    init_schema()
    with connect() as conn:
        events = source.fetch(req.log_group, start, now)
        sid = create_session(conn, req.log_group, start, now, kind="baseline")
        summary = ingest_signatures(
            conn, embedder, req.log_group, sid, events, skip_high_severity=True
        )
        set_session_status(conn, sid, "complete")
        pruned = prune_baseline_sessions(conn, req.log_group)
        kept = baseline_session_count(conn, req.log_group)

    # A fresh learn invalidates any cached incident analysis for this group.
    _analysis_cache.pop(req.log_group, None)

    return {
        "log_group": req.log_group,
        "events": len(events),
        "learned": summary["distinct"],
        "skipped_severe": summary["skipped"],
        "baseline_sessions": kept,
        "pruned": pruned,
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.refresh and req.log_group in _analysis_cache:
        return _analysis_cache[req.log_group]

    config = load_config()
    source = CloudWatchLogSource(_logs_client())
    embedder = build_embedder(config)

    init_schema()
    with connect() as conn:
        # Honest empty state: ranking against no baseline would flag everything
        # as novel, so require a baseline first.
        if baseline_session_count(conn, req.log_group) == 0:
            raise HTTPException(
                status_code=409,
                detail="No baseline learned for this service yet. Run learn first.",
            )

        now = datetime.now(timezone.utc)
        incident_start = now - timedelta(minutes=INCIDENT_MINUTES)

        events = source.fetch(req.log_group, incident_start, now)
        isid = create_session(conn, req.log_group, incident_start, now, kind="incident")
        ingest_signatures(conn, embedder, req.log_group, isid, events)
        candidates = rank_session(conn, req.log_group, isid)
        set_session_status(conn, isid, "complete")

    result = {
        "session_id": isid,
        "log_group": req.log_group,
        "incident_events": len(events),
        "candidates": [
            {
                "rank": i + 1,
                "template": c.template,
                "composite": c.composite,
                "incident_count": c.incident_count,
                "novelty": c.novelty,
                "rate_change": c.rate_change,
                "severity": c.severity,
            }
            for i, c in enumerate(candidates)
        ],
    }
    _analysis_cache[req.log_group] = result
    return result


@app.post("/verdict")
def verdict(req: VerdictRequest):
    config = load_config()
    generator = build_verdict_generator(config)
    with connect() as conn:
        result = generate_verdict_for_rank(conn, generator, req.session_id, req.rank)
    if result is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return result
