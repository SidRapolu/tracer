"""
ingestion/source.py

The LogEvent record and the LogSource interface.

Everything above ingestion depends only on this contract, never on a
concrete backend. The real CloudWatch source and the offline fixture source
both implement fetch() identically, so callers cannot tell which one they
hold. That decoupling is also what lets the eval harness run on fixtures and
get identical inputs every time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class LogEvent:
    timestamp: datetime 
    message: str 
    log_group: str 
    log_stream: Optional[str] = None


class LogSource(ABC):
    # A source of log events over a time window.
    @abstractmethod
    def fetch(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
    ) -> list[LogEvent]:
        ...
