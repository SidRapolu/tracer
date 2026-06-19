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
from ranking.rank import rank_session
from sessions.db import connect, init_schema
from sessions.models import create_session, set_session_status
from signatures.store import ingest_signatures
from verdicts.orchestrate import generate_verdict_for_rank

# Auto-baseline windows: baseline is a lookback ending before the incident
# window; incident is the most recent slice.
BASELINE_MINUTES = 30
INCIDENT_MINUTES = 5

app = FastAPI(title="Tracer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-process cache: group name -> analyze result. Re-clicking a group
# returns the cached ranking instead of re-spending on ingestion/ranking.
_analysis_cache: dict[str, dict] = {}


def _region() -> str:
    return load_config().aws_region


def _logs_client():
    return boto3.client("logs", region_name=_region())


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


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.refresh and req.log_group in _analysis_cache:
        return _analysis_cache[req.log_group]

    config = load_config()
    client = _logs_client()
    source = CloudWatchLogSource(client)
    embedder = build_embedder(config)

    now = datetime.now(timezone.utc)
    incident_start = now - timedelta(minutes=INCIDENT_MINUTES)
    baseline_start = incident_start - timedelta(minutes=BASELINE_MINUTES)

    init_schema()
    with connect() as conn:
        # Baseline from the earlier lookback window.
        b_events = source.fetch(req.log_group, baseline_start, incident_start)
        bsid = create_session(conn, req.log_group, baseline_start, incident_start, kind="baseline")
        ingest_signatures(conn, embedder, req.log_group, bsid, b_events)
        set_session_status(conn, bsid, "complete")

        # Incident from the recent window.
        i_events = source.fetch(req.log_group, incident_start, now)
        isid = create_session(conn, req.log_group, incident_start, now, kind="incident")
        ingest_signatures(conn, embedder, req.log_group, isid, i_events)
        candidates = rank_session(conn, req.log_group, isid)
        set_session_status(conn, isid, "complete")

    result = {
        "session_id": isid,
        "log_group": req.log_group,
        "baseline_events": len(b_events),
        "incident_events": len(i_events),
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
