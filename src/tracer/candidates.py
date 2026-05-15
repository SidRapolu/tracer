"""
Candidate scoring & ranking.

For each signature seen in an analyze session, we compute three component
scores and combine them into a total. The components are deliberately
separable so they can be inspected, tuned, and displayed individually in
the eventual UI ("ranked here because: novel + high severity").

  - novelty_score [0..1]: how new this signature is for this log group,
    relative to historical baseline (1.0 = never seen before).
  - rate_score    [0..1]: how loud this signature was in this session
    compared to its historical average when present (1.0 = ≥3x normal,
    or for new signatures, a strong burst).
  - severity_score [0..1]: how serious the level is (FATAL=1, ERROR=.85,
    WARN=.4, INFO/DEBUG=.1).

Weights are intentionally exposed so that Day 6's feedback loop can
adjust them per log group over time.
"""

import math

from . import baseline, storage


DEFAULT_WEIGHTS = {
    "novelty": 0.40,
    "rate": 0.30,
    "severity": 0.30,
}

SEVERITY_SCORES = {
    "FATAL": 1.0,
    "ERROR": 0.85,
    "WARN": 0.40,
    "INFO": 0.10,
    "DEBUG": 0.05,
}


def _novelty(stats: dict) -> float:
    """
    1.0 if signature has never appeared in any prior session.
    Approaches 0 as it appears in more prior sessions.
    """
    if stats["sessions_total"] == 0:
        # No baseline at all yet — we have nothing to compare against.
        # Treat as "moderately novel" so first-run scores aren't all 1.0.
        return 0.5
    return max(0.0, 1.0 - stats["frequency"])


def _rate(current_count: int, stats: dict) -> float:
    """
    For a signature with prior history, compare current count to historical
    avg-when-seen. ≥3x = 1.0, 1x = 0.0, scales linearly between.

    For a brand-new signature, use a log-scaled burst score: a single
    occurrence is mildly interesting, double-digit occurrences are very.
    """
    avg = stats["avg_count_per_appearance"]
    if avg <= 0:
        # Brand-new (or never seen) signature. log-scale the count.
        # count=1 → ~0.23; count=10 → ~0.80; count≥20 → 1.0.
        return min(1.0, math.log(current_count + 1) / math.log(20))
    ratio = current_count / avg
    if ratio <= 1.0:
        return 0.0
    return min(1.0, (ratio - 1.0) / 2.0)


def _severity(level: str) -> float:
    return SEVERITY_SCORES.get(level, 0.1)


def score_session(conn, session_id: str, log_group: str,
                  session_counts: dict[int, int],
                  weights: dict[str, float] | None = None,
                  min_score: float = 0.15,
                  top_n: int = 25) -> list[dict]:
    """
    Score every signature that appeared in this session, rank by total
    score, persist to candidates table, and return the ranked list.

    min_score filters out the long tail of obviously-uninteresting
    signatures (normal INFO logs). top_n caps how many we persist;
    most users only ever look at the top few.
    """
    w = weights or DEFAULT_WEIGHTS

    scored: list[dict] = []
    for sig_id, count in session_counts.items():
        sig = storage.get_signature(conn, sig_id)
        stats = baseline.baseline_for_signature(
            conn, sig_id, log_group, session_id
        )

        novelty = _novelty(stats)
        rate = _rate(count, stats)
        severity = _severity(sig["severity"])

        total = (
            w["novelty"] * novelty
            + w["rate"] * rate
            + w["severity"] * severity
        )

        scored.append({
            "signature_id": sig_id,
            "signature_text": sig["signature_text"],
            "severity": sig["severity"],
            "session_count": count,
            "novelty_score": novelty,
            "rate_score": rate,
            "severity_score": severity,
            "total_score": total,
            "baseline_frequency": stats["frequency"],
            "baseline_avg": stats["avg_count_per_appearance"],
            "sessions_prior": stats["sessions_prior"],
        })

    scored.sort(key=lambda r: r["total_score"], reverse=True)

    kept = [r for r in scored if r["total_score"] >= min_score][:top_n]
    for i, row in enumerate(kept, start=1):
        row["rank"] = i

    if kept:
        storage.insert_candidates(conn, session_id, kept)

    return kept
