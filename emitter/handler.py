from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import boto3

LOG_GROUP = os.environ.get("DEMO_LOG_GROUP", "/tracer/demo")
BURST_EVERY = int(os.environ.get("BURST_EVERY", "5"))
COUNTER_FILE = Path("/tmp/tracer_emitter_count")

logs = boto3.client("logs")

# Normal traffic: drawn each run so baselines see steady, varied-but-familiar
# lines. IDs/durations vary so normalization has something to collapse.
NORMAL_TEMPLATES = [
    "INFO health check ok",
    "INFO request id={id} completed in {ms}ms",
    "INFO cache hit for key=sku-{n}",
    "DEBUG worker heartbeat",
    "WARN slow query took {ms}ms on orders",
]

# Incident lines, injected on burst runs. Repeated NPE plus a downstream
# timeout — the shapes the ranker should surface above the normal traffic.
INCIDENT_TEMPLATES = [
    "ERROR NullPointerException at PaymentProcessor.charge line 88",
    "ERROR NullPointerException at PaymentProcessor.charge line 88",
    "ERROR NullPointerException at PaymentProcessor.charge line 88",
    "ERROR downstream timeout calling fraud-service after 3000ms",
]


def _render(template: str) -> str:
    return template.format(
        id=f"{random.randint(0, 0xFFFFF):05x}",
        ms=random.randint(20, 950),
        n=random.randint(1, 99),
    )


def _next_count() -> int:
    n = 0
    if COUNTER_FILE.exists():
        try:
            n = int(COUNTER_FILE.read_text())
        except ValueError:
            n = 0
    n += 1
    COUNTER_FILE.write_text(str(n))
    return n


def _ensure_stream(stream_name: str) -> None:
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    try:
        logs.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream_name)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


def handler(event, context):
    count = _next_count()
    is_burst = (count % BURST_EVERY) == 0

    messages = [_render(t) for t in random.choices(NORMAL_TEMPLATES, k=random.randint(8, 14))]
    if is_burst:
        messages += [_render(t) for t in INCIDENT_TEMPLATES]
        random.shuffle(messages)

    stream_name = time.strftime("%Y/%m/%d/[$LATEST]") + f"{random.randint(0, 0xFFFF):04x}"
    _ensure_stream(stream_name)

    now_ms = int(time.time() * 1000)
    log_events = [{"timestamp": now_ms, "message": m} for m in messages]

    logs.put_log_events(
        logGroupName=LOG_GROUP,
        logStreamName=stream_name,
        logEvents=log_events,
    )

    return {
        "invocation": count,
        "burst": is_burst,
        "lines_written": len(messages),
        "log_group": LOG_GROUP,
    }
