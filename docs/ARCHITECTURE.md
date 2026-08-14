# Architecture

## Shape

```
        inbound                    FlowPilot process                     outbound
  ┌───────────────┐        ┌───────────────────────────────┐      ┌──────────────────┐
  │ web form      │        │ webhooks.py                   │      │ CRM (REST)       │
  │ WhatsApp      │  HMAC  │   verify signature + skew     │      │ Slack / Discord  │
  │ email parser  ├───────►│ workflow.py                   ├─────►│ your webhook     │
  │ chat widget   │        │   7 steps, retries, tracing   │      │ enrichment API   │
  │ your API      │        │ ai.py         integrations.py │      └──────────────────┘
  └───────────────┘        │        db.py (SQLite)         │
                           │ api.py ──► dashboard (SVG)    │
                           └───────────────────────────────┘
```

One process, four layers, no hidden state. Each layer is replaceable without touching the others:
swap SQLite for Postgres in `db.py`, swap Claude for another model in `ai.py`, add a channel by
teaching `step_ingest` one more shape.

## Data model

| Table | Row per | Notes |
|---|---|---|
| `runs` | workflow execution | status, duration, source, signature-verified flag, AI mode, tokens, cost, the original payload and the final lead |
| `run_steps` | step within a run | status, attempts, duration, error, raw output — this is what the trace drawer renders |
| `leads` | triaged contact | extraction + score + tier + owner + queue + SLA + reply draft |
| `deliveries` | outbound call | target, endpoint, ok, status code, latency, simulated flag |

Keeping `runs.payload_json` is what makes **replay** possible: the stored payload is re-run
through the *current* workflow definition, which is how you validate a scoring change against
real historical traffic before shipping it.

## Failure model

Three levels, chosen per step:

1. **Retry** — `retries=n` inside the step, with linear backoff. Transient network blips.
2. **Optional** — the step is allowed to fail; the run is marked `partial` and continues.
   Used for `enrich`, `ai_reply`, `deliver`: none of them should be able to lose a lead that
   has already been captured and scored.
3. **Fatal** — everything else. `ingest` failing means there is nothing to process, so the run
   stops there and the webhook returns 500.

Underneath, `integrations.http_call` adds its own bounded exponential-backoff retry with a
timeout, retries only 5xx and transport errors (never 4xx), and writes an audit row either way.
The AI layer has a fourth mode: on any Claude error it degrades to the rule engine and records
`heuristic-fallback` plus the error on the run, so a model outage costs classification quality
rather than availability.

## Security

- **Inbound**: HMAC-SHA256 over `"<timestamp>." + raw_body`, constant-time compare, ±300s
  replay window. Verification is enforced whenever `WEBHOOK_SECRET` is set; when it is not,
  requests are accepted but every run is flagged `unverified` in the UI rather than silently
  trusted.
- **Outbound**: the same scheme, so downstream consumers can authenticate our events.
- Secrets come from the environment only. Message bodies are capped at 8 000 characters before
  reaching the model. All dashboard rendering is HTML-escaped, and the JSON API is
  parameterised throughout — no string-built SQL.

## What I would change for production traffic

| Now | Then | Why |
|---|---|---|
| SQLite | Postgres | concurrent writers; SQLite's single-writer lock is the first thing to break |
| Synchronous `deliver` | queue (Redis/RQ, SQS) with a durable outbox | keeps the webhook response fast and survives a CRM outage without losing events |
| Flask dev server | gunicorn/uvicorn behind nginx | the dev server is single-process and not hardened |
| Per-request Claude call | batch the low-urgency tail; cache identical bodies | the Batches API is half price for anything not latency-sensitive |
| In-process rate limiting: none | per-source token bucket on the webhook | an inbound flood currently costs money proportional to traffic |
| Prompt versioning: none | version the prompt + schema alongside the run | so a scoring shift can be attributed to a prompt change |
| Logs to stdout | structured logs + traces keyed by `run_id` | `run_id` is already threaded through every layer, so this is a sink change |

## Cost

Every run records its own `input_tokens`, `output_tokens` and `cost_usd`, so the dashboard's AI
spend tile is measured, not modelled. Two Claude calls per lead (extraction + reply) at Opus
list prices land in the low single-digit cents; the reply call is skipped for anything archived
as spam, and dropping the extraction step to a cheaper model is a one-line change in `.env`
(`AI_MODEL`) if a client's volume justifies it.
