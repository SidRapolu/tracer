from __future__ import annotations

import math
import re
from dataclasses import dataclass

W_NOVELTY = 0.45
W_RATE = 0.35
W_SEVERITY = 0.20

# Distance below which a "novel" signature is treated as semantically known.
# pgvector cosine distance: 0 = identical, 1 = orthogonal.
SEMANTIC_KNOWN_DISTANCE = 0.15

_SEVERITY_LEVEL = {"FATAL": 1.0, "ERROR": 0.85, "CRITICAL": 0.85, "WARN": 0.5, "WARNING": 0.5}
_SEVERITY_TOKENS = ("exception", "timeout", "panic", "fatal", "refused", "denied", "oom")


@dataclass
class Candidate:
    signature_id: int
    template: str
    incident_count: int
    novelty: float
    rate_change: float
    severity: float
    composite: float

# Lexical severity from log level plus high-signal tokens.
def severity_score(template: str) -> float:
    upper = template.upper()
    level = 0.0
    for word, value in _SEVERITY_LEVEL.items():
        if re.search(rf"\b{word}\b", upper):
            level = max(level, value)
    lower = template.lower()
    token_hits = sum(1 for t in _SEVERITY_TOKENS if t in lower)
    token_boost = min(0.3, 0.1 * token_hits)
    return min(1.0, level + token_boost)

# 1.0 for a signature with no baseline history, reduced toward 0 when a near
# semantic neighbor exists. A signature with history scores 0.
def novelty_score(has_baseline_history: bool, nearest_distance: float | None) -> float:
    if has_baseline_history:
        return 0.0
    if nearest_distance is not None and nearest_distance < SEMANTIC_KNOWN_DISTANCE:
        # Semantically close to a known signature: scale down proportionally.
        return nearest_distance / SEMANTIC_KNOWN_DISTANCE
    return 1.0

# Elevation of incident frequency over baseline average, squashed to 0..1.
# A signature with no baseline (avg 0) but real incident volume reads as a
# strong spike; tanh keeps it bounded.
def rate_change_score(incident_count: int, baseline_avg: float) -> float:

    if incident_count <= 0:
        return 0.0
    ratio = incident_count / (baseline_avg + 1.0)  # +1 avoids div-by-zero, damps tiny counts
    return math.tanh(ratio / 5.0)

# Returns weighted sum, weights chosen so novelty and rate lead and severity breaks ties.
def composite_score(novelty: float, rate_change: float, severity: float) -> float:
    return W_NOVELTY * novelty + W_RATE * rate_change + W_SEVERITY * severity
