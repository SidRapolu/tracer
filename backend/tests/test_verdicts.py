from __future__ import annotations

import io
import json

from verdicts.generate import VerdictGenerator, _parse_verdict, _build_prompt


def test_parse_plain_json():
    text = '{"verdict_line":"a","hypothesis":"b","next_step":"c"}'
    assert _parse_verdict(text) == {"verdict_line": "a", "hypothesis": "b", "next_step": "c"}


def test_parse_json_with_markdown_fence():
    # Claude sometimes wraps JSON in a ```json fence despite instructions.
    text = '```json\n{"verdict_line":"a","hypothesis":"b","next_step":"c"}\n```'
    assert _parse_verdict(text)["verdict_line"] == "a"


def test_prompt_includes_samples_and_template():
    prompt = _build_prompt({
        "template": "ERROR NPE at X line <N>",
        "incident_count": 4,
        "composite": 0.87,
        "sample_lines": ["a | b | c"],
    })
    assert "ERROR NPE" in prompt
    assert "0.87" in prompt
    assert "a | b | c" in prompt


class _FakeBedrock:
    """Returns a Claude-shaped invoke_model response with a JSON verdict."""

    def invoke_model(self, modelId, body):
        verdict = {"verdict_line": "Null charge", "hypothesis": "missing customer",
                   "next_step": "check upstream"}
        payload = {"content": [{"text": json.dumps(verdict)}]}
        return {"body": io.BytesIO(json.dumps(payload).encode())}


def test_generate_returns_structured_verdict():
    gen = VerdictGenerator(_FakeBedrock())
    v = gen.generate({
        "template": "ERROR NPE", "incident_count": 4,
        "composite": 0.87, "sample_lines": ["x | y"],
    })
    assert v.verdict_line == "Null charge"
    assert v.hypothesis == "missing customer"
    assert v.next_step == "check upstream"
