"""
Generate a realistic CloudWatch log fixture for testing tracer.

The fixture intentionally contains:
  - High-frequency normal traffic (heartbeats, request logs)
  - Recurring warnings the tool should learn as baseline noise
  - Sporadic old errors that have been seen before
  - A planted "incident" in the last 10 minutes: a burst of NEW errors

Run this script to regenerate fixtures/sample_logs.json.
"""

import json
import random
import uuid
from pathlib import Path

SEED = 42
random.seed(SEED)

# All offsets are seconds before "now" (computed at fixture load time).
# Negative = in the past.

WINDOW_SECONDS = 2 * 60 * 60  # 2 hours of history
INCIDENT_START = 10 * 60      # incident begins 10 min before "now"


def make_uuid():
    return str(uuid.uuid4())


def make_customer_id():
    return f"cust_{random.randint(100000, 999999)}"


def event(offset_seconds: int, message: str, stream: str = "main-stream"):
    return {
        "offset_seconds": -offset_seconds,
        "message": message,
        "logStreamName": stream,
    }


events = []

# --- Normal traffic spanning the full 2-hour window ---
# Heartbeats every ~30s
for t in range(0, WINDOW_SECONDS, 30):
    events.append(event(t, "INFO Health check OK"))

# Request logs scattered throughout (~2 per minute on average)
for t in range(0, WINDOW_SECONDS, 30):
    if random.random() < 0.9:
        cust = make_customer_id()
        events.append(event(t + random.randint(0, 25),
                            f"INFO Request received GET /api/v1/offers customer_id={cust}"))
        events.append(event(t + random.randint(0, 25),
                            f"INFO Request completed status=200 latency_ms={random.randint(20, 180)}"))

# Cache activity
for t in range(0, WINDOW_SECONDS, 45):
    if random.random() < 0.7:
        events.append(event(t + random.randint(0, 40),
                            f"INFO Cache hit key=offer:{make_uuid()}"))

# Connection pool stats every 5 min
for t in range(0, WINDOW_SECONDS, 300):
    events.append(event(t,
                        f"DEBUG Connection pool stats active={random.randint(2, 8)} idle={random.randint(5, 15)}"))

# --- Recurring warnings (baseline noise) ---
# Slow queries — happen throughout
for _ in range(18):
    t = random.randint(60, WINDOW_SECONDS)
    events.append(event(t,
                        f"WARN Slow query took {random.randint(800, 2400)}ms query=SELECT_OFFERS_BY_SEGMENT"))

# DynamoDB throttling — happens occasionally
for _ in range(8):
    t = random.randint(60, WINDOW_SECONDS)
    events.append(event(t,
                        f"WARN DynamoDB throttling backoff_ms={random.randint(50, 400)}"))

# --- Sporadic old errors (transient, known) ---
for _ in range(4):
    t = random.randint(60 * 30, WINDOW_SECONDS)  # only in the older half
    events.append(event(t,
                        "ERROR Timeout calling segmentation-service after 5000ms"))

# --- The INCIDENT: new errors clustered in the last 10 minutes ---
incident_messages = [
    "ERROR NullPointerException at OfferEligibilityService.evaluate:142",
    "ERROR java.lang.NullPointerException: customerSegment is null",
    "ERROR Failed to evaluate offer customer_id={cust} reason=null_segment",
]

for _ in range(14):
    t = random.randint(0, INCIDENT_START)
    msg_template = random.choice(incident_messages)
    msg = msg_template.format(cust=make_customer_id())
    events.append(event(t, msg, stream="incident-stream"))

# A few cascading effects from the incident (also new but secondary)
for _ in range(3):
    t = random.randint(0, INCIDENT_START - 60)
    events.append(event(t,
                        f"ERROR Request completed status=500 latency_ms={random.randint(5000, 15000)}"))

# Sort by timestamp (most recent first, like CloudWatch typically returns)
events.sort(key=lambda e: e["offset_seconds"], reverse=True)


output = {
    "description": "Synthetic CloudWatch logs for tracer testing. "
                   "Contains 2 hours of normal traffic with a planted "
                   "NullPointerException incident in the last 10 minutes.",
    "events": events,
}

out_path = Path(__file__).parent / "sample_logs.json"
with open(out_path, "w") as f:
    json.dump(output, f, indent=2)

print(f"Wrote {len(events)} events to {out_path}")
print(f"  Time span: {WINDOW_SECONDS}s ({WINDOW_SECONDS // 60} min)")
print(f"  Incident window: last {INCIDENT_START // 60} min")
