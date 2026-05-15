"""
Signature extraction.

A "signature" is a normalized form of a log message where variable data
(UUIDs, numbers, IDs, timestamps, file paths) has been replaced with stable
placeholders. The goal is that two log lines that differ only in their
variable data collapse to the same signature, which is what lets us count
recurrences and learn baselines.

Examples:
  "ERROR Failed to evaluate offer customer_id=cust_843211 reason=null_segment"
  "ERROR Failed to evaluate offer customer_id=cust_120554 reason=null_segment"
  → both become:
  "ERROR Failed to evaluate offer customer_id=<ID> reason=null_segment"

The replacement order matters: more specific patterns first, generic
patterns last, otherwise generic patterns (like \\d+) would steal characters
from more specific ones (like a UUID's digits).

This is signature-hashing rather than embedding-based clustering. It's
deterministic, has no model dependencies, and is the right starting
point for the MVP. The clustering interface in this module is set up so
an embedding-based grouper can be slotted in later without changes
elsewhere.
"""

import re

_NORMALIZERS: list[tuple[re.Pattern, str]] = [
    # ISO timestamps with optional fractional seconds and zone
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    # UUIDs (standard 8-4-4-4-12)
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    # IPv4
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b"), "<IP>"),
    # Patterned IDs like cust_123456, user_abc123, order_42
    (re.compile(r"\b[a-z]{2,12}_[A-Za-z0-9]{4,}\b"), "<ID>"),
    # Long hex strings (request IDs, hashes)
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<HEX>"),
    # File paths (anything with a slash and an extension)
    (re.compile(r"(?:/[\w.\-]+)+\.\w+"), "<PATH>"),
    # Quoted strings
    (re.compile(r'"[^"]*"'), "<STR>"),
    (re.compile(r"'[^']*'"), "<STR>"),
    # Standalone numbers (after the more specific patterns have run)
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|h|kb|mb|gb)?\b", re.IGNORECASE), "<NUM>"),
]


def extract_signature(message: str) -> str:
    """Normalize a log message into a signature."""
    sig = message
    for pattern, placeholder in _NORMALIZERS:
        sig = pattern.sub(placeholder, sig)
    # Collapse runs of whitespace
    sig = re.sub(r"\s+", " ", sig).strip()
    return sig



_SEVERITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(?:\[)?FATAL\b", re.IGNORECASE), "FATAL"),
    (re.compile(r"^\s*(?:\[)?ERROR\b", re.IGNORECASE), "ERROR"),
    (re.compile(r"^\s*(?:\[)?WARN(?:ING)?\b", re.IGNORECASE), "WARN"),
    (re.compile(r"^\s*(?:\[)?INFO\b", re.IGNORECASE), "INFO"),
    (re.compile(r"^\s*(?:\[)?DEBUG\b", re.IGNORECASE), "DEBUG"),
    (re.compile(r"^\s*(?:\[)?TRACE\b", re.IGNORECASE), "DEBUG"),
]

_EXCEPTION_HINT = re.compile(
    # Exception/Error/Throwable match as suffixes too (NullPointerException,
    # ValueError, etc.), which is why there's no leading \b on those.
    # Panic/Traceback are normally standalone words.
    r"(?:Exception|Error|Throwable)\b|\b(?:Panic|Traceback)\b"
)


def extract_severity(message: str) -> str:
    """Classify a message's severity."""
    for pattern, level in _SEVERITY_PATTERNS:
        if pattern.search(message):
            return level
    # If no explicit level marker but the message looks like an exception,
    # call it ERROR. This catches stack-trace lines without a prefix.
    if _EXCEPTION_HINT.search(message):
        return "ERROR"
    return "INFO"



def signaturize(events: list[dict]) -> list[dict]:
    """
    Add 'signature' and 'severity' fields to each event in-place-style.
    Returns the same list with the new fields added.
    """
    for e in events:
        e["signature"] = extract_signature(e["message"])
        e["severity"] = extract_severity(e["message"])
    return events
