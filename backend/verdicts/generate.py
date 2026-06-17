from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Claude Sonnet 4.6 on Bedrock — fast and cheap enough for a few calls per
# analyze, capable enough for structured log reasoning. 
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
ANTHROPIC_VERSION = "bedrock-2023-05-31"
MAX_TOKENS = 512


# One structured verdict for a ranked candidate.
@dataclass
class Verdict:
    verdict_line: str   # one-sentence "what this is"
    hypothesis: str     # likely cause given the surrounding context
    next_step: str      # concrete next action to investigate/confirm


_SYSTEM = (
    "You are a debugging assistant analyzing a ranked log signature from an "
    "incident. Given the signature template, its scores, and sample raw log "
    "lines surrounding its occurrences, produce a concise structured verdict. "
    "Respond ONLY with a JSON object with keys verdict_line, hypothesis, "
    "next_step. No markdown, no preamble."
)


def _build_prompt(candidate: dict[str, Any]) -> str:
    samples = "\n".join(f"  - {s}" for s in candidate.get("sample_lines", [])) or "  (none)"
    return (
        f"Signature template: {candidate['template']}\n"
        f"Occurrences in incident window: {candidate['incident_count']}\n"
        f"Anomaly score (0-1, higher = more anomalous): {candidate['composite']}\n"
        f"Sample surrounding log lines:\n{samples}\n"
    )


class VerdictGenerator:
    """Generates structured verdicts via Claude on Bedrock. Client is injected."""

    def __init__(self, client: Any, model_id: str = MODEL_ID) -> None:
        self._client = client
        self._model_id = model_id

    def generate(self, candidate: dict[str, Any]) -> Verdict:
        body = {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": MAX_TOKENS,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": _build_prompt(candidate)}],
        }
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"]
        data = _parse_verdict(text)
        return Verdict(
            verdict_line=data["verdict_line"],
            hypothesis=data["hypothesis"],
            next_step=data["next_step"],
        )


# Parse the model's JSON reply, tolerating stray markdown fences if present.
def _parse_verdict(text: str) -> dict[str, str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
