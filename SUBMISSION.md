# Practical Technical Test — AI Automation Developer

**Candidate:** Kanishq
**Company:** I Vision Infotech
**Submission:** FlowPilot — an inbound lead & support triage automation, plus its control-room
dashboard. One repository covers all five required items; every claim below points at code you
can run.

Start here: [README.md](README.md) · run it in 30 seconds, no API keys needed.

---

## 1. Custom AI Automation Workflow — *build 1 complete workflow*

**`app/workflow.py` — `inbound_lead_triage`, seven steps, end to end.**

| # | Step | What it does |
|---|---|---|
| 1 | `ingest` | Normalises web form / WhatsApp / email / chat widget / API into one shape; rejects empty payloads before any paid call. |
| 2 | `ai_extract` | Claude returns a **strict JSON schema**: intent, category, urgency, sentiment, spam score, name, email, phone, company, budget, timeline, one-line summary, key points. |
| 3 | `enrich` | Company enrichment from the email domain over REST (free-mail detection, company name, country). |
| 4 | `score` | Deterministic 0–100 score → `hot` / `warm` / `cold` / `archived`, with the reasoning recorded per lead. |
| 5 | `route` | Queue + owner + an SLA deadline derived from the tier (hot 2h, warm 8h, cold 48h). |
| 6 | `ai_reply` | Claude drafts the first response, channel- and intent-aware. |
| 7 | `deliver` | CRM upsert + notification + signed outbound webhook, then persist. |

What makes it a *workflow* rather than a script: per-step retry budgets, per-step timing and
attempt counts, optional steps that degrade a run to `partial` instead of losing the lead, a
full persisted trace, and **replay** — any stored payload can be re-run through the current
workflow definition and the score comes out identical (there is a test for that).

Deliberate design point: the model classifies, the rules decide. Scoring and routing are plain
Python so a client can audit exactly why a lead was called hot.

---

## 2. Third-Party API Integration — *integrate 1 REST API or webhook*

Four integrations, all through one hardened client (`app/integrations.py`) with timeouts,
bounded exponential-backoff retries, and an audit row per call surfaced in the dashboard.

- **Inbound webhook** — `POST /webhook/inbound`, HMAC-SHA256 with the timestamp inside the MAC,
  a ±300s replay window and constant-time comparison. Five rejection cases are unit-tested.
- **CRM (REST)** — `POST {CRM_BASE_URL}/contacts` with bearer auth.
- **Notification webhook** — Slack / Discord / Teams / any HTTPS endpoint (the payload field is
  chosen per provider).
- **Outbound event webhook** — signed with the same scheme we verify inbound, so downstream
  systems can trust it.

`scripts/mock_crm_server.py` implements all three outbound endpoints locally — including
verifying our outbound signature — so the live path is demonstrable with no third-party
account. Verified working end to end: CRM `HTTP 200`, notification delivered, outbound webhook
`signature_verified: true`.

---

## 3. Custom Dashboard — *develop 1 functional dashboard*

`http://127.0.0.1:5000` — a live control room, not a mockup.

- Six KPIs: leads triaged, hot leads, automation rate, average end-to-end handling time, spam
  filtered, AI spend (tokens × list price).
- Inbound volume vs. hot leads over time, lead-quality mix, leads by channel, top intents,
  per-integration health (calls, success rate, latency, live vs simulated).
- Filters (range / tier / channel / search), CSV export, and a **Simulate inbound lead** button
  that fires a real run so the workflow can be demonstrated in the interview.
- Clicking any lead opens the **full workflow trace**: every step with status, duration, retry
  count and raw output; the original message; the AI-drafted reply; the outbound calls; and a
  Replay button.

Built with hand-rolled SVG — no chart library, no CDN, no external fonts. Light and dark are
both explicitly designed; every chart has a table view; the categorical and ordinal palettes
were validated for colour-vision deficiency and contrast against both surfaces.

Screenshots: [light](docs/dashboard-light.png) · [dark](docs/dashboard-dark.png) ·
[workflow trace](docs/run-trace.png).

---

## 4. Real Client Automation Flow — *showcase 1 real client automation or a similar self-developed project*

FlowPilot is the **self-developed project** submitted under this item. The write-up —
the problem it solves, the before/after operating model, the decisions I'd defend in a review,
and honest limits — is in **[docs/CASE_STUDY.md](docs/CASE_STUDY.md)**.

The scenario it is modelled on (a services business drowning in mixed-quality inbound) is
representative rather than a specific named client engagement, and the case study says so
plainly. Every number quoted in it is measured from this system, not estimated.

---

## 5. GitHub / Portfolio / Live Demo

| | Link |
|---|---|
| **Repository** | https://github.com/kanishqtanwar35-hub/flowpilot-ai-automation — *create it with this name and push (commands below)* |
| **What it does, in plain language** | [docs/WHAT_IT_DOES.md](docs/WHAT_IT_DOES.md) — the problem, the use case, a worked example |
| **Live demo** | https://claude.ai/code/artifact/5ebab5ae-4583-48da-86d5-23c4181d4516 — the real dashboard with real run data, no backend required. Charts, filters, table views and the full workflow trace all work. *(Private by default: open it and use the page's share menu before sending the link.)* |
| **Same demo, self-hosted** | `docs/demo.html` — one self-contained file; open it directly or serve it from GitHub Pages |
| **Run it locally** | `pip install -r requirements.txt && python scripts/seed_demo.py --reset && python run.py` |

```bash
git init
python scripts/scan_secrets.py --install-hook     # blocks any commit containing a secret
git add . && git commit -m "FlowPilot — AI automation suite"
git branch -M main
git remote add origin https://github.com/kanishqtanwar35-hub/flowpilot-ai-automation.git
git push -u origin main
# GitHub Pages: Settings → Pages → deploy from main /docs
#   → https://kanishqtanwar35-hub.github.io/flowpilot-ai-automation/demo.html
```

Create the empty repo first at https://github.com/new (name: `flowpilot-ai-automation`, no
README/licence — this repo already has them). Pages gives you a live demo link on your own
domain, which reads better on an application than a share link.

---

## Bonus items covered

| Asked for | Where |
|---|---|
| AI Agent / OpenAI–Gemini integration | Claude (`claude-opus-5`) via the Anthropic SDK with a strict JSON schema, retries and an automatic rule-engine fallback — `app/ai.py` |
| AI Dashboard | the control room above, including live AI cost/token accounting per run |
| Custom CRM | lead store, scoring, ownership, queues and SLA tracking — `app/db.py`, `app/workflow.py`, plus a REST CRM push |
| WhatsApp automation | WhatsApp is a first-class inbound channel; the workflow is channel-aware end to end (a Cloud API sender drops into the `deliver` step) |
| AI Chatbot | the reply-drafting step is the first half of it; the same extraction + routing core is what a chatbot would sit on |

---

## Honest notes

- No `ANTHROPIC_API_KEY` was available in the environment where this was built, so the Claude
  calls are covered by tests against a stub SDK client rather than by a live call. The rule-engine
  fallback is what produced the seeded data — which is why the dashboard reports AI spend as $0.
  Add a key and the same code path runs for real, with cost and tokens appearing per run.
- SQLite and Flask's dev server are demo-appropriate, not production. What I'd change for real
  traffic is written down in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) rather than left implied.

## Secret handling

The key is never in the repo, the logs, the database, the API, or the published demo — see
**[docs/SECURITY.md](docs/SECURITY.md)**. Two layers of redaction at the single choke point
(`app/security.py`), credential-bearing URLs stored via `safe_url()`, and a pre-commit hook
(`python scripts/scan_secrets.py --install-hook`) that blocks a commit containing a secret.
Proven end to end: with an invalid key loaded, the live `401` from Anthropic is caught, the run
completes on the fallback engine, and no key material reaches the stored trace.

**Tests:** `python -m pytest -q` → 32 passing.
