"""
Baseline tracking.

After Day 2 signatures are extracted, this module is responsible for:
  - Persisting signatures + per-session occurrence counts to storage
  - Answering questions like "how often does signature X normally appear
    per session in this log group?" — which is what candidate scoring
    needs.

The baseline lives in the database, not in memory. The tool gets smarter
across runs because of this: every analyze session contributes to what
"normal" looks like for the log group.
"""

from collections import Counter

from . import storage


def record_session_signatures(conn, session_id: str, log_group: str,
                              events_with_sigs: list[dict]) -> dict:
    """
    Persist signatures and occurrence counts for one session's worth of
    events. Returns a dict of signature_id -> count for this session,
    which Day 3 candidate scoring will use.

    Each event must already have 'signature' and 'severity' fields
    (set by signatures.signaturize).
    """
    # Map (signature_text, severity) -> count for this session
    sig_to_events: dict[tuple[str, str], list[dict]] = {}
    for ev in events_with_sigs:
        key = (ev["signature"], ev["severity"])
        sig_to_events.setdefault(key, []).append(ev)

    session_counts: dict[int, int] = {}

    for (sig_text, severity), evs in sig_to_events.items():
        # Use earliest timestamp in this batch for first/last_seen tracking
        earliest = min(e["timestamp_ms"] for e in evs)
        latest = max(e["timestamp_ms"] for e in evs)

        sig_id = storage.upsert_signature(
            conn, log_group, sig_text, severity, latest
        )
        # If this is the first time we've seen the signature, first_seen
        # gets set to `latest` by upsert. Patch it down to `earliest` so
        # first_seen accurately reflects the earliest occurrence we saw.
        conn.execute(
            "UPDATE signatures SET first_seen_ms = MIN(first_seen_ms, ?) "
            "WHERE id = ?",
            (earliest, sig_id),
        )

        # Backfill signature_id on the underlying log_events rows.
        # We do this in bulk per signature to avoid N updates.
        event_ids = [e["id"] for e in evs]
        placeholders = ",".join("?" * len(event_ids))
        conn.execute(
            f"UPDATE log_events SET signature_id = ? "
            f"WHERE id IN ({placeholders})",
            [sig_id, *event_ids],
        )

        count = len(evs)
        storage.record_signature_occurrence(conn, sig_id, session_id, count)
        session_counts[sig_id] = count

    return session_counts


def baseline_for_signature(conn, signature_id: int, log_group: str,
                           current_session_id: str) -> dict:
    """
    Return baseline statistics for a signature, excluding the current
    session so the current session doesn't pollute its own baseline.

    Keys returned:
      total_prior:        total occurrences in prior sessions
      sessions_prior:     number of prior sessions in which it appeared
      sessions_total:     total prior analyze sessions for this group
      avg_per_session:    sessions_prior / sessions_total, in [0, 1].
                          A value of 1.0 means it appears every session
                          (definitely baseline noise).
                          A value of 0.0 means it has never appeared
                          before (brand new).
      avg_count_per_appearance: total_prior / sessions_prior when seen
    """
    stats = storage.historical_signature_stats(
        conn, signature_id, current_session_id
    )
    total_sessions = storage.historical_session_count_for_group(
        conn, log_group, current_session_id
    )

    sessions_prior = stats["sessions_present"]
    total_prior = stats["total"]

    if total_sessions == 0:
        frequency = 0.0  # No baseline yet
    else:
        frequency = sessions_prior / total_sessions

    avg_when_seen = (total_prior / sessions_prior) if sessions_prior else 0.0

    return {
        "total_prior": total_prior,
        "sessions_prior": sessions_prior,
        "sessions_total": total_sessions,
        "frequency": frequency,
        "avg_count_per_appearance": avg_when_seen,
    }
