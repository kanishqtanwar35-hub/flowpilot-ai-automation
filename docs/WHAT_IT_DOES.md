# What FlowPilot does, and who it's for

*A plain-language explainer. No code — the technical detail is in
[ARCHITECTURE.md](ARCHITECTURE.md).*

---

## In one sentence

FlowPilot reads every enquiry a business receives — from any channel — decides how important it
is, routes it to the right person with a deadline, and drafts the first reply, in under a second,
before anyone has opened the inbox.

---

## The use case

**Who this is for:** any business where enquiries arrive faster than a human can read them, and
where the person qualified to judge a lead is also the person doing billable work.

Typically that means a services business — an agency, consultancy, B2B supplier, clinic,
logistics firm — with roughly **50–500 inbound messages a month** arriving through more than one
channel, and no dedicated triage person.

**The specific moment it fixes:** a good lead arrives at 7pm on Friday. Under the old process it
is read Monday afternoon. By then the prospect has messaged two competitors.

### The problem, concretely

Enquiries come through five doors — website form, WhatsApp, shared inbox, chat widget, partner
APIs — in five different shapes, into one pile. What goes wrong is unglamorous, which is why it
survives for years:

| What happens | Cost |
|---|---|
| Roughly **1 in 6 messages is spam** (SEO, backlinks, crypto pitches) | Senior attention spent on messages that were never going to convert |
| **No priority order** | A paying customer's outage queues behind a student's CV — both are just unread messages |
| **Triage is batched** — twice a day, when someone has a gap | Hours of dead time on every enquiry, including the urgent ones |
| **Nothing is measured** | Nobody can answer "how many enquiries last month, how many were qualified, how fast did we reply" without reading a mailbox |

The instinct is to buy a CRM. But a CRM stores what you have *already* classified — it doesn't do
the classifying. And hand-written keyword rules break the first time someone writes *"your thing
stopped working"* instead of *"bug"*.

---

## What actually happens to a message

Seven steps, about a second end to end. Here is a real enquiry from the demo, start to finish.

> **Incoming (website form):** *"Hi, we're a 40-person logistics firm and want to automate quote
> requests coming in over WhatsApp and email into our CRM. Budget around $15,000, need it live in
> 4 weeks. Can we talk this week?"*

| # | Step | What it does with that message |
|---|---|---|
| 1 | **Ingest** | Normalises it into one shape, whichever channel it came from |
| 2 | **Read it** (AI) | Pulls out: intent `pricing question`, urgency `high`, sentiment `neutral`, spam probability `0.03`, name, email, **budget $15,000**, **timeline 4 weeks**, one-line summary |
| 3 | **Enrich** | `vertexfoods.com` isn't a free mailbox → real business, company name recorded |
| 4 | **Score** | `intent +45`, `urgency:high +15`, `business email +10`, `budget stated +12`, `timeline stated +8` → **90 / 100 → HOT** |
| 5 | **Route** | Sales queue, named owner, **2-hour deadline** stamped on it |
| 6 | **Draft the reply** (AI) | A first response referencing their actual ask, ready for a human to send |
| 7 | **Deliver** | Pushed to the CRM, owner pinged on Slack, event sent to any other system listening |

**The same run, for a spam message:**

> *"Hello sir, we provide 5000 high quality backlinks and guaranteed page 1 ranking, very cheap
> price."*

Spam probability `0.92` → score penalty ×0.08 → **score 0, archived, no owner, no reply drafted,
never routed to a human.** It's counted on the dashboard and then it's gone.

---

## What changes for the business

| | Before | After |
|---|---|---|
| Triage | Manual, batched twice a day | Automatic, on arrival |
| Spam | Read, then deleted | Scored, archived, never seen |
| Priority | First-in-first-out | 0–100 score, four tiers, a deadline per tier |
| First response | Ad hoc | Drafted per enquiry; owner notified immediately |
| Data | A mailbox | Scored records, full history, CSV export |
| Visibility | None | Live dashboard: volume, quality, channel, intent, what's overdue |

**It does not replace the human.** It moves them from *reading everything* to *acting on a
scored queue* — and it means the 7pm Friday lead gets an owner and an acknowledgement at 7pm
Friday.

---

## What you watch it through

A live dashboard (the "control room"):

- **Six numbers at the top** — enquiries triaged, hot leads, automation rate, average handling
  time, spam filtered, AI cost.
- **Trends** — volume vs. hot leads over time, quality mix, which channels produce what, which
  intents dominate.
- **A queue** — every lead with score, tier, owner and time left on its SLA; overdue in red.
- **A full trace per lead** — every one of the seven steps with its timing, what the AI extracted,
  exactly why the score came out as it did, the drafted reply, and every outbound call. Click any
  lead to see it.
- **Integration health** — which outbound systems are up, how fast, and how many calls succeeded.

That trace is the part clients care about most: when someone asks *"why was this called hot?"*,
the answer is a list of reasons, not a shrug.

---

## Where it plugs in

**Messages come in from:** website forms, WhatsApp, email, chat widgets, partner APIs — anything
that can send an HTTP request. Each sender is authenticated with a signed request, so the public
endpoint can't be spammed with forged traffic.

**Results go out to:** a CRM (any REST API), a notification channel (Slack, Discord, Teams), and
a signed webhook for anything else you want to hook up later — billing, analytics, a scheduler.

Nothing is hard-wired. Each destination is a setting; if one is not configured, that step is
skipped and clearly marked as such rather than failing the whole run.

---

## The one design decision worth explaining

**The AI reads; the rules decide.**

The model handles what code is bad at: understanding messy human text. Scoring, routing and
deadlines stay in ordinary code, because:

- a client needs to audit *why* a lead was prioritised — and get the same answer every time;
- rules cost nothing to run and never have a bad day;
- tuning them is a code change you can review, not prompt guesswork.

And if the AI is unreachable — outage, expired key, rate limit — a rule engine takes over
automatically. Classification quality drops; **nothing is lost, and nothing stops.**

---

## What it deliberately doesn't do

- **It doesn't send the reply.** It drafts it. Auto-sending to a prospect is a business decision,
  not a default — a human approves, or you add an approval step.
- **It doesn't decide who wins.** It prioritises attention. Whether a lead closes is still sales.
- **It isn't a CRM replacement.** It's the layer *in front* of one — it does the classifying a CRM
  assumes you've already done.
- **Its scoring weights are opinions, not truths.** They're right for the scenario above and
  should be re-tuned against a real client's won/lost history.

---

## The same engine, other jobs

The pipeline — *receive → understand → score → route → act* — isn't specific to sales enquiries.
Swapping the fields and the rules covers:

| Use case | What changes |
|---|---|
| **Support ticket triage** | Score on severity and customer tier instead of buying intent; route to on-call |
| **Recruitment inbox** | Extract skills and experience; route by role; auto-archive off-target applications |
| **Partner / vendor requests** | Score on contract value and urgency; route to the right account owner |
| **Order and claim intake** | Extract order numbers and amounts; validate; push to fulfilment |

The workflow engine, the scoring framework, the dashboard and the integration layer are unchanged.
Only the extraction schema and the rules table move.

---

## See it in 30 seconds

**Live demo (nothing to install):**
https://claude.ai/code/artifact/5ebab5ae-4583-48da-86d5-23c4181d4516

**Or run it yourself:**

```bash
pip install -r requirements.txt
python scripts/seed_demo.py --reset     # 70 realistic enquiries across 14 days
python run.py                           # dashboard at http://127.0.0.1:5000
```

Click **Simulate inbound lead** to push a real message through the workflow and watch the trace
appear. No API keys needed — it runs end to end offline.

**Read next:** [README](../README.md) (how it's built) · [ARCHITECTURE](ARCHITECTURE.md) (technical
design) · [CASE_STUDY](CASE_STUDY.md) (the numbers) · [SECURITY](SECURITY.md) (how the keys are
protected).
