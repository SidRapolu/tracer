"""
ingestion/cloudwatch.py

The real CloudWatch backend, built on boto3's filter_log_events.

Two details matter here:

  - Pagination. filter_log_events caps each response and returns a nextToken
    when more remains. The loop follows it to exhaustion; skipping it gives
    silently truncated results.
  - Epoch milliseconds. CloudWatch timestamps are epoch ms; Python's
    datetime.timestamp() is float seconds. The conversion helpers keep that
    boundary in one place.

The boto3 client is injected so tests can pass a fake implementing the same
filter_log_events shape, exercising pagination and timestamp logic with no
AWS calls. filter_log_events (not Logs Insights) is used because Tracer wants
the raw event stream for a window and does its own ranking on top.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ingestion.source import LogEvent, LogSource


def _to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class CloudWatchLogSource(LogSource):
    # Fetches log events from a real CloudWatch log group.

    def __init__(self, client: Any) -> None:
        self._client = client

    def fetch(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
    ) -> list[LogEvent]:
        events: list[LogEvent] = []
        next_token: Optional[str] = None

        while True:
            kwargs: dict[str, Any] = {
                "logGroupName": log_group,
                "startTime": _to_epoch_ms(start),
                "endTime": _to_epoch_ms(end),
            }
            if next_token:
                kwargs["nextToken"] = next_token

            response = self._client.filter_log_events(**kwargs)

            for raw in response.get("events", []):
                events.append(
                    LogEvent(
                        timestamp=_from_epoch_ms(raw["timestamp"]),
                        message=raw["message"],
                        log_group=log_group,
                        log_stream=raw.get("logStreamName"),
                    )
                )

            next_token = response.get("nextToken")
            if not next_token:
                break

        events.sort(key=lambda e: e.timestamp)
        return events
