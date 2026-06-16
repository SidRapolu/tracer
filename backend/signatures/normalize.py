from __future__ import annotations

import hashlib
import re

# Order matters: more specific patterns run before more general ones so a
# UUID isn't half-eaten by the hex rule, etc.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b\d+(?:\.\d+)?ms\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?s\b"), "<DUR>"),
    (re.compile(r"\bline \d+\b"), "line <N>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),
    # Tokens that are mixed letters+digits (e.g. request ids like a91, d01).
    (re.compile(r"\b(?=[a-zA-Z]*\d)(?=\d*[a-zA-Z])[a-zA-Z0-9]{2,}\b"), "<ID>"),
    # Bare numbers last, so they don't pre-empt the typed rules above.
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<N>"),
]


def normalize(message: str) -> str:
    # Return the stable template for a raw log line.
    text = message.strip()
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    # Collapse whitespace so spacing differences don't fork the template.
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(template: str) -> str:
    # Stable short hash of a template, used as the exact-match key.
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]
