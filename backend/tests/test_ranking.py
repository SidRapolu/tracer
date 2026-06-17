"""
tests/test_ranking.py

Offline unit tests for the scorer. Pure arithmetic, no DB or AWS, so the
scoring logic is pinned down deterministically here; the live ranking query
path is exercised via the CLI against Postgres.
"""

from __future__ import annotations

from ranking.score import (
    composite_score,
    novelty_score,
    rate_change_score,
    severity_score,
)


def test_no_history_is_fully_novel():
    assert novelty_score(has_baseline_history=False, nearest_distance=None) == 1.0


def test_history_is_not_novel():
    assert novelty_score(has_baseline_history=True, nearest_distance=None) == 0.0


def test_semantically_known_reduces_novelty():
    # A "new" signature whose nearest baseline neighbor is very close is scaled
    # down rather than treated as fully novel.
    score = novelty_score(has_baseline_history=False, nearest_distance=0.03)
    assert 0.0 < score < 1.0


def test_rate_change_rises_with_spike():
    calm = rate_change_score(incident_count=2, baseline_avg=2.0)
    spike = rate_change_score(incident_count=50, baseline_avg=2.0)
    assert spike > calm
    assert 0.0 <= spike <= 1.0


def test_rate_change_zero_when_absent():
    assert rate_change_score(incident_count=0, baseline_avg=5.0) == 0.0


def test_severity_orders_levels():
    err = severity_score("ERROR NullPointerException at X")
    warn = severity_score("WARN slow query took <DUR>")
    info = severity_score("INFO health check ok")
    assert err > warn > info


def test_severity_token_boost():
    # An ERROR with a high-signal token outscores a bare ERROR.
    plain = severity_score("ERROR something happened")
    tokened = severity_score("ERROR downstream timeout calling x")
    assert tokened >= plain


def test_composite_weights_sum_sensibly():
    # All-max signals approach 1.0; all-zero is 0.0.
    assert composite_score(1.0, 1.0, 1.0) == 1.0
    assert composite_score(0.0, 0.0, 0.0) == 0.0


def test_incident_error_outranks_baseline_noise():
    # A novel high-severity spike should outscore familiar low-severity noise.
    incident = composite_score(
        novelty_score(False, None),
        rate_change_score(8, 0.0),
        severity_score("ERROR NullPointerException at PaymentProcessor.charge"),
    )
    noise = composite_score(
        novelty_score(True, None),
        rate_change_score(3, 3.0),
        severity_score("INFO health check ok"),
    )
    assert incident > noise
