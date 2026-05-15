"""
Pulling logs from CloudWatch (or its mock). The client is injected by the
caller so this module never knows or cares whether it's running against
real AWS or the FakeLogsClient.
"""

import time
import uuid


def fetch_logs(client, log_group: str,
               start_ms: int, end_ms: int,
               max_events: int = 50000) -> tuple[str, list[dict]]:
    """
    Pull events from CloudWatch via filter_log_events. Returns
    (session_id, events).

    The session_id is generated here so the caller can immediately attach
    storage rows to it.
    """
    session_id = str(uuid.uuid4())
    events: list[dict] = []
    next_token: str | None = None

    while True:
        kwargs = {
            "logGroupName": log_group,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 10000,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        response = client.filter_log_events(**kwargs)
        events.extend(response.get("events", []))

        if len(events) >= max_events:
            events = events[:max_events]
            break

        next_token = response.get("nextToken")
        if not next_token:
            break

    return session_id, events


def window_from_args(window_minutes: int, ago_minutes: int = 0) -> tuple[int, int]:
    """
    Compute (start_ms, end_ms) for a window of `window_minutes` ending
    `ago_minutes` before now. ago=0 means "ending now".
    """
    now_ms = int(time.time() * 1000)
    end_ms = now_ms - (ago_minutes * 60 * 1000)
    start_ms = end_ms - (window_minutes * 60 * 1000)
    return start_ms, end_ms
