from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from ingestion.source import LogEvent, LogSource


def _parse_ts(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class FixtureLogSource(LogSource):
    # Format log events from a JSON fixture file.

    def __init__(self, path: Union[str, Path]) -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        self._events = sorted(
            (
                LogEvent(
                    timestamp=_parse_ts(raw["timestamp"]),
                    message=raw["message"],
                    log_group=raw["log_group"],
                    log_stream=raw.get("log_stream"),
                )
                for raw in data["events"]
            ),
            key=lambda e: e.timestamp,
        )

    def fetch(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
    ) -> list[LogEvent]:
        return [
            e
            for e in self._events
            if e.log_group == log_group and start <= e.timestamp < end
        ]
