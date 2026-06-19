# Tracer

A debugging assistant for AWS CloudWatch. Point it at a log group and it ranks
the most likely root causes of an incident, then writes a short verdict for the
top few — instead of scrolling thousands of log lines by hand.

## Why

Grepping for `ERROR` finds errors; it doesn't tell you which one is the
incident. Tracer learns what a service's logs normally look like and ranks by
deviation from that baseline, so a new failure surfaces above the benign error
that fires every minute.

## How it works

```
CloudWatch logs
      │
      ▼
  normalize        "request id=a91 took 42ms" ─┐
  into signatures  "request id=b12 took 38ms" ─┴─▶ one signature
      │
      ▼
  rank vs baseline   novelty · rate change · severity
      │
      ▼
  explain top 3      Claude reads each + raw context → verdict, cause, next step
```

- **Novelty** is exact-match against the baseline, backed by embeddings: a line
  that's new wording but the same meaning as a known one isn't flagged.
- **Embeddings** (Titan → pgvector) are computed only when exact-match misses.
- **Ranking** is deterministic and explainable; the LLM runs only on the top 3.
- **Accuracy** is measured — a labeled set of incidents reports top-1 / top-3.

## Stack

Python · AWS (CloudWatch, Bedrock, Lambda) · Postgres + pgvector · React

- Bedrock Titan (`amazon.titan-embed-text-v2:0`) — embeddings
- Bedrock Claude (Sonnet 4.6) — verdicts
- pgvector — nearest-neighbor over signatures; relational data lives in the
  same Postgres, no separate vector DB

## Demo emitter

A Lambda _emits_ incident-shaped logs (normal traffic with periodic error bursts) to a
dedicated `/tracer/demo` group, which gives the baseline a quiet period to learn
and anomalies to surface. Lambda, schedule, log group, and a least-privilege IAM
role are defined in Terraform — `terraform apply`.

## Run

```bash
cd backend && pip install -r requirements.txt

docker run -d --name tracer-pg -p 5432:5432 \
  -e POSTGRES_USER=tracer -e POSTGRES_PASSWORD=REPLACE_WITH_PASSWORD \
  -e POSTGRES_DB=tracer pgvector/pgvector:pg16

# copy .env.example to .env, set DB password + AWS region

python cli.py analyze --service /tracer/demo --kind baseline --start ... --end ...
python cli.py analyze --service /tracer/demo --kind incident --start ... --end ...

PYTHONPATH=backend python eval/run_eval.py   # measure ranking accuracy
```

Defaults to a local fixture; `--source cloudwatch` runs against real log groups.

## Scope

One service window at a time. No multi-tenant isolation, cost controls, or
production retries — kept out to keep the core idea sharp.
