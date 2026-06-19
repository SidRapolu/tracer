from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

# How far back to look when counting errors for the group list.
GROUP_SCAN_MINUTES = 30
# A line counts as an error if it matches this (level word or common tokens).
_ERROR_RE = re.compile(r"\b(ERROR|FATAL|CRITICAL)\b|exception|timeout|panic", re.IGNORECASE)


# List log groups with a recent error count, most errors first. Uses the same
# authenticated logs client the rest of the backend uses.
def list_groups_by_error_volume(client: Any, minutes: int = GROUP_SCAN_MINUTES) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    groups = []
    paginator = client.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for g in page.get("logGroups", []):
            groups.append(g["logGroupName"])

    results = []
    for name in groups:
        error_count = _count_errors(client, name, start_ms, end_ms)
        results.append({"log_group": name, "error_count": error_count})

    results.sort(key=lambda r: r["error_count"], reverse=True)
    return results


def _count_errors(client: Any, log_group: str, start_ms: int, end_ms: int) -> int:
    count = 0
    next_token = None
    while True:
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_ms,
            "endTime": end_ms,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.filter_log_events(**kwargs)
        except Exception:
            # A group we can't read shouldn't break the whole listing.
            return count
        for event in response.get("events", []):
            if _ERROR_RE.search(event.get("message", "")):
                count += 1
        next_token = response.get("nextToken")
        if not next_token:
            break
    return count
