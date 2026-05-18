# Tracer

Interactive AWS CloudWatch debugging tool that ranks candidate root causes from
log traces using learned per-service baselines.

(LLM verdict layer, React UI, feedback loop, eval harness) are
designed for/development in progress.

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

