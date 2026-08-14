# Case study — FlowPilot inbound triage

**What this is.** A self-developed automation, submitted for the "real client automation flow or
a similar self-developed project" item. The operating problem below is the one this class of
business actually has, and it is the brief I built against — but it is a **representative
scenario, not a named client engagement**, and I would rather say that up front than dress it up.

Everything under *Measured* is read off this system. Everything under *Modelled* is arithmetic
with its assumptions stated, so you can disagree with the assumptions rather than the sales pitch.

---

## The problem

A services business — agency, consultancy, B2B supplier — takes enquiries through five doors:
the website form, WhatsApp, a shared inbox, a chat widget, and partner APIs. They arrive in
five different shapes, in one shared inbox, and somebody senior triages them by hand.

The failure isn't dramatic, which is why it survives:

- **Mixed quality.** Roughly a sixth of it is SEO/backlink/crypto spam. It still gets read.
- **No priority.** A production outage from a paying customer queues behind a student's CV
  because both are unread emails.
- **Slow first touch.** The person who can judge a lead is also the person doing billable work,
  so triage happens in batches, twice a day.
- **Nothing is measured.** No one can answer "how many enquiries last month, how many were
  qualified, how fast did we reply" without reading a mailbox.

The instinct is to buy a CRM. A CRM stores what you already classified; it doesn't do the
classifying, and hand-written keyword rules break the first time someone writes "your thing
stopped working" instead of "bug".

## The approach

One workflow behind every channel, splitting the work by what each half is actually good at:

- **The model reads.** Claude turns free text into structured fields — intent, category,
  urgency, sentiment, spam probability, contact details, budget, timeline, a one-line summary —
  under a strict JSON schema, so downstream code gets the same shape every time.
- **Rules decide.** Scoring, tiering, routing and SLAs are plain Python. When the client asks
  "why was this called hot", the answer is a list — `intent:new_business +55`,
  `urgency:critical +25`, `business email +10`, `budget stated +12` — not "the model felt
  strongly". Tuning weights is a code review, not prompt archaeology.
- **Delivery is best-effort, capture is not.** CRM push, notification and event fan-out are all
  optional steps. If Slack is down the lead is still captured, scored and stored; the run is
  marked `partial` and shows on the dashboard as a delivery failure to retry.
- **Everything is traceable.** Each run keeps its payload, its per-step timings and retries, and
  its outbound calls. Any run can be replayed through the current workflow, which is how a
  scoring change gets validated against real historical traffic before it ships.

## Before / after

| | Before | After |
|---|---|---|
| Triage | manual, batched twice daily | automatic, on arrival |
| Spam | read, then deleted | scored, archived, never routed |
| Priority | first-in-first-out | 0–100 score, four tiers, SLA per tier |
| First response | ad hoc | drafted per lead, owner notified immediately |
| Data | a mailbox | scored lead records + full run traces + CSV export |
| Visibility | none | live KPIs, per-channel/intent breakdowns, integration health |

## Measured (this system, 70-run seeded window)

| Metric | Value |
|---|---|
| End-to-end workflow time, webhook → CRM | **~5 ms** on the rule engine; ~2 s with live REST integrations against a local endpoint |
| Runs completing without a failed step | **100%** |
| Spam identified and auto-archived | **12 of 70 (17%)** — never routed to a human |
| Qualified (hot + warm) | **42 of 70 (60%)** |
| Steps traced per run | **7**, each with status, duration and attempt count |
| Test suite | **24 passing** |

The dominant cost is the model call, not the workflow: with Claude enabled, extraction plus
reply drafting is the whole latency budget (typically a few seconds), and every run records its
own token count and dollar cost so the spend tile is measured rather than modelled.

## Modelled (stated assumptions, not claims)

Assume 300 enquiries/month, 8 minutes of senior time per manual triage, and the 17% spam rate
measured above. That is 40 hours/month of triage, of which ~7 hours is spent on spam. Automating
classification and first-response drafting doesn't remove the human — it moves them from
*reading everything* to *acting on a scored queue*. Judge the number by the assumptions: if your
spam rate is 3% and triage takes 2 minutes, the case is much weaker, and you should not buy this.

Where it genuinely pays is the tail: the hot lead that arrived at 7pm Friday and got a reply
Monday afternoon.

## What I'd defend in a review

- **Two AI calls per lead, not one.** Extraction and reply drafting want different prompts and
  different failure handling — the reply is skipped entirely for spam, and it can fail without
  affecting the lead record.
- **Scoring stays out of the model.** Deterministic, replayable, explainable, free.
- **The fallback is a feature.** No API key, an outage or a refusal all degrade to a rule engine
  that still produces a routable lead. Classification quality drops; availability does not.
- **Signed webhooks in both directions.** The endpoint is public; the timestamp is inside the
  MAC and the window is ±300s, so a captured request can't be replayed.

## Honest limits

- Scoring weights are opinions. They are correct for the scenario above and should be re-tuned
  per client against their closed-won data.
- The heuristic fallback is genuinely worse than the model at anything indirect — it reads
  keywords, so "your thing stopped working" classifies weakly. It is a floor, not a substitute.
- SQLite and Flask's dev server are demo-appropriate. The production changes are listed in
  [ARCHITECTURE.md](ARCHITECTURE.md).
- No live Claude key was available in the build environment, so the model path is covered by
  tests against a stub SDK client and the seeded data came from the rule engine.

## If this were a client engagement, next

1. Re-tune the score against their last 12 months of closed-won/closed-lost.
2. Add outbound WhatsApp (Cloud API) so the drafted reply can actually send, behind an approval
   step for anything above a confidence threshold.
3. Move `deliver` onto a queue with a durable outbox so a CRM outage retries instead of degrading.
4. Weekly digest: what came in, what converted, where the SLA slipped — the data is already there.
