# Tracer

Interactive AWS CloudWatch debugging tool that ranks candidate root causes from
log traces using learned per-service baselines.

## Status

Days 1–3 implemented against a mocked CloudWatch client. No real AWS account
required to run.

- **Day 1** — Plumbing: ingestion path, dependency-injected client, SQLite
  storage with session tracking.
- **Day 2** — Signatures & baselines: normalize log lines into stable
  signatures, track per-signature occurrence counts across sessions.
- **Day 3** — Candidate ranking: score signatures in a session by novelty,
  rate change, and severity to produce a ranked list of candidate causes.

Days 4–7 (LLM verdict layer, React UI, feedback loop, eval harness) are
designed for but not yet built.

## Setup

```bash
cd tracer
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Try it

Run an analyze session against the fake CloudWatch fixture:

```bash
# Pull "logs" for a 15-minute window and run signature analysis
tracer analyze --fake --log-group /aws/lambda/offers-service --window 60

# Then run it again over a more recent (overlapping but shifted) window
# to see how baselines learn and which signatures become "known noise"
tracer analyze --fake --log-group /aws/lambda/offers-service --window 15
```

On the second run, recurring signatures get downweighted as baseline noise,
and the new errors that only appear in the incident window surface as
top-ranked candidates.

## When you're ready to use real AWS

Drop the `--fake` flag and set up an AWS profile named `tracer` (or pass
`--profile your-profile`) with `logs:FilterLogEvents`,
`logs:DescribeLogGroups`, and `logs:GetLogEvents` permissions. The rest of
the code path is identical.

## Layout

```
tracer/
├── src/tracer/
│   ├── ingest.py       # CloudWatch fetching (client injected)
│   ├── storage.py      # SQLite schema + read/write
│   ├── fake_aws.py     # FakeLogsClient backed by JSON fixture
│   ├── signatures.py   # Normalize messages into stable signatures
│   ├── baseline.py     # Track signature occurrence over time
│   ├── candidates.py   # Score and rank candidates for a session
│   └── cli.py          # CLI entry point (analyze, history, inspect)
├── fixtures/
│   └── sample_logs.json
└── data/
    └── tracer.db       # SQLite (created on first run, gitignored)
```
