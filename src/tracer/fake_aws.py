"""
Fake CloudWatch Logs client for offline development.

Mimics the subset of boto3's logs client that tracer uses (filter_log_events).
The fixture file stores events with offsets relative to "now"; the client
resolves them to absolute timestamps when instantiated, so a single fixture
can be used at any wall-clock time.

The real boto3 client returns events with keys: timestamp (ms), message,
logStreamName, ingestionTime, eventId. We match the first three, which are
the only ones tracer uses.
"""

import json
import time
from pathlib import Path


class FakeLogsClient:
    def __init__(self, fixture_path: Path, now_ms: int | None = None):
        with open(fixture_path) as f:
            fixture = json.load(f)

        anchor = now_ms if now_ms is not None else int(time.time() * 1000)
        self._events = [
            {
                "timestamp": anchor + (e["offset_seconds"] * 1000),
                "message": e["message"],
                "logStreamName": e["logStreamName"],
            }
            for e in fixture["events"]
        ]
        # CloudWatch typically returns events in ascending-timestamp order
        # when using filter_log_events with default settings.
        self._events.sort(key=lambda e: e["timestamp"])

    def filter_log_events(self, **kwargs):
        """
        Subset of the real API. Honors logGroupName (ignored — we only have
        one fixture), startTime, endTime, limit, and nextToken.
        """
        start = kwargs.get("startTime", 0)
        end = kwargs.get("endTime", 2**63 - 1)
        limit = kwargs.get("limit", 10000)
        next_token = kwargs.get("nextToken")

        matched = [e for e in self._events
                   if start <= e["timestamp"] <= end]

        # Simple pagination: nextToken is the index to start from.
        offset = int(next_token) if next_token else 0
        page = matched[offset:offset + limit]

        response = {"events": page}
        if offset + limit < len(matched):
            response["nextToken"] = str(offset + limit)
        return response

    def describe_log_groups(self, **kwargs):
        # Stub to match the API surface; not used by tracer's main path.
        return {"logGroups": [{"logGroupName": "/aws/lambda/fake-service"}]}
