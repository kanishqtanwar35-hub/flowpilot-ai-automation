"""The automation workflow (deliverable #1).

A tiny, explicit engine — no Celery, no DAG framework — because the point is to
show the control flow, not to hide it:

    ingest → ai_extract → enrich → score → route → ai_reply → deliver

Every step is retried independently, timed, and persisted, so the dashboard can
replay exactly what happened. A step marked ``optional`` degrades the run to
``partial`` instead of failing it: an unreachable Slack webhook must never lose
a lead that was already captured and scored.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import db, integrations
from .ai import AIEngine
from .security import redact

log = logging.getLogger("flowpilot.workflow")

# --------------------------------------------------------------------------- #
# Business rules — the deterministic half of the system. Kept out of the model
# on purpose: scoring and routing must be auditable and identical every run.
# --------------------------------------------------------------------------- #
INTENT_BASE = {
    "new_business": 55, "pricing_question": 45, "partnership": 35,
    "support_issue": 40, "recruitment": 10, "other": 20, "spam": 0,
}
URGENCY_BONUS = {"critical": 25, "high": 15, "normal": 5, "low": 0}
SENTIMENT_ADJ = {"negative": 8, "neutral": 0, "positive": 3}  # angry customers escalate

ROUTING = {
    "new_business":     ("Sales",   "Aditi Rao"),
    "pricing_question": ("Sales",   "Aditi Rao"),
    "partnership":      ("Growth",  "Kanishq"),
    "support_issue":    ("Support", "Rohit Menon"),
    "recruitment":      ("People",  "HR Inbox"),
    "spam":             ("Archive", "Unassigned"),
    "other":            ("Triage",  "Kanishq"),
}
SLA_HOURS = {"hot": 2, "warm": 8, "cold": 48, "archived": 0}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
@dataclass
class Context:
    run_id: str
    payload: dict
    source: str
    verified: bool
    data: dict = field(default_factory=dict)
    ai: AIEngine = field(default_factory=AIEngine)
    ai_mode: str = "heuristic"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def account(self, result) -> None:
        """Roll model usage up to the run so the dashboard can cost it."""
        self.ai_mode = result.mode
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cost_usd += result.cost_usd


@dataclass
class Step:
    name: str
    title: str
    fn: Callable[[Context], dict]
    retries: int = 0
    optional: bool = False


class WorkflowError(Exception):
    pass


class Workflow:
    def __init__(self, name: str, steps: list[Step]):
        self.name = name
        self.steps = steps

    def run(self, payload: dict, source: str = "api", verified: bool = False) -> dict:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        ctx = Context(run_id=run_id, payload=payload, source=source, verified=verified)
        records: list[dict] = []
        started_at, t0 = now(), time.perf_counter()
        status = "success"
        run_error: str | None = None

        for step in self.steps:
            attempts, s_started = 0, time.perf_counter()
            step_status, output, error = "success", None, None

            while True:
                attempts += 1
                try:
                    output = step.fn(ctx) or {}
                    break
                except Exception as exc:  # noqa: BLE001 - retry policy lives here
                    # redact before it is persisted, rendered in the trace, or logged
                    error = redact(f"{type(exc).__name__}: {exc}")
                    if attempts <= step.retries:
                        log.warning("step %s failed (attempt %s): %s", step.name, attempts, error)
                        time.sleep(0.3 * attempts)
                        continue
                    step_status = "skipped" if step.optional else "failed"
                    log.error("step %s %s: %s", step.name, step_status, error)
                    break

            records.append(
                {
                    "name": step.name,
                    "title": step.title,
                    "status": step_status,
                    "attempts": attempts,
                    "duration_ms": int((time.perf_counter() - s_started) * 1000),
                    "error": error if step_status != "success" else None,
                    "output": output,
                }
            )

            if step_status == "failed":
                status, run_error = "failed", error
                break
            if step_status == "skipped":
                status = "partial"

        finished_at = now()
        run = {
            "id": run_id,
            "workflow": self.name,
            "source": source,
            "status": status,
            "verified": 1 if verified else 0,
            "started_at": iso(started_at),
            "finished_at": iso(finished_at),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "error": run_error,
            "lead_id": ctx.data.get("lead", {}).get("id"),
            "ai_mode": ctx.ai_mode,
            "input_tokens": ctx.input_tokens,
            "output_tokens": ctx.output_tokens,
            "cost_usd": round(ctx.cost_usd, 6),
            "payload": payload,
            "result": ctx.data.get("lead"),
        }
        db.save_run(run, records)
        run["steps"] = records
        return run


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
CHANNELS = {"web_form", "whatsapp", "email", "chat_widget", "phone", "api"}


def step_ingest(ctx: Context) -> dict:
    p = ctx.payload or {}
    message = (p.get("message") or p.get("text") or p.get("body") or "").strip()
    if not message:
        raise WorkflowError("payload has no message/text/body field")
    if len(message) > 8000:
        message = message[:8000]

    channel = (p.get("channel") or "api").lower()
    if channel not in CHANNELS:
        channel = "api"

    ctx.data["normalised"] = {
        "message": message,
        "channel": channel,
        "meta": {
            "name": (p.get("name") or p.get("full_name") or "").strip() or None,
            "email": (p.get("email") or "").strip().lower() or None,
            "phone": (p.get("phone") or "").strip() or None,
            "company": (p.get("company") or "").strip() or None,
            "external_id": p.get("id") or p.get("external_id"),
        },
        "received_at": iso(now()),
    }
    return {"channel": channel, "chars": len(message), "verified": ctx.verified}


def step_ai_extract(ctx: Context) -> dict:
    n = ctx.data["normalised"]
    result = ctx.ai.extract(n["message"], n["channel"], n["meta"])
    ctx.account(result)
    ctx.data["extracted"] = result.data
    return {
        "mode": result.mode,
        "latency_ms": result.latency_ms,
        "tokens": {"in": result.input_tokens, "out": result.output_tokens},
        "note": result.note,
        **{k: result.data[k] for k in ("intent", "category", "urgency", "sentiment", "spam_score")},
    }


def step_enrich(ctx: Context) -> dict:
    email = ctx.data["extracted"].get("email") or ctx.data["normalised"]["meta"].get("email")
    result = integrations.enrich_company(email, ctx.run_id)
    info = result.get("response") or {}
    ctx.data["enrichment"] = info
    return {"simulated": result.get("simulated"), **({k: info.get(k) for k in list(info)[:6]})}


def step_score(ctx: Context) -> dict:
    x = ctx.data["extracted"]
    enrich = ctx.data.get("enrichment") or {}

    score = INTENT_BASE.get(x["intent"], 20)
    score += URGENCY_BONUS.get(x["urgency"], 0)
    score += SENTIMENT_ADJ.get(x["sentiment"], 0)
    reasons = [
        f"intent:{x['intent']} +{INTENT_BASE.get(x['intent'], 20)}",
        f"urgency:{x['urgency']} +{URGENCY_BONUS.get(x['urgency'], 0)}",
    ]
    if enrich.get("is_business_email"):
        score += 10
        reasons.append("business email +10")
    if x.get("budget"):
        score += 12
        reasons.append("budget stated +12")
    if x.get("timeline"):
        score += 8
        reasons.append("timeline stated +8")
    if x.get("phone"):
        score += 4
        reasons.append("phone given +4")
    if x["spam_score"] >= 0.6:
        score = int(score * (1 - x["spam_score"]))
        reasons.append(f"spam penalty x{1 - x['spam_score']:.2f}")

    score = max(0, min(100, int(score)))
    if x["intent"] == "spam" or x["spam_score"] >= 0.75:
        tier = "archived"
    elif score >= 70:
        tier = "hot"
    elif score >= 45:
        tier = "warm"
    else:
        tier = "cold"

    ctx.data["scoring"] = {"score": score, "tier": tier, "reasons": reasons}
    return ctx.data["scoring"]


def step_route(ctx: Context) -> dict:
    x, s = ctx.data["extracted"], ctx.data["scoring"]
    enrich = ctx.data.get("enrichment") or {}
    queue, owner = ROUTING.get(x["intent"], ("Triage", "Kanishq"))
    if s["tier"] == "hot" and queue == "Sales":
        owner = "Kanishq"  # founder-led follow-up on hot leads
    sla_hours = SLA_HOURS[s["tier"]]
    due = now() + timedelta(hours=sla_hours) if sla_hours else now()

    lead = {
        "id": f"lead_{uuid.uuid4().hex[:10]}",
        "created_at": iso(now()),
        "channel": ctx.data["normalised"]["channel"],
        "name": x.get("person_name") or ctx.data["normalised"]["meta"].get("name"),
        "email": x.get("email"),
        "phone": x.get("phone"),
        "company": x.get("company") or enrich.get("company_guess"),
        "country": enrich.get("country"),
        "message": ctx.data["normalised"]["message"],
        "intent": x["intent"],
        "category": x["category"],
        "sentiment": x["sentiment"],
        "urgency": x["urgency"],
        "budget": x.get("budget"),
        "timeline": x.get("timeline"),
        "spam_score": x["spam_score"],
        "score": s["score"],
        "tier": s["tier"],
        "owner": owner,
        "queue": queue,
        "sla_due_at": iso(due),
        "status": "archived" if s["tier"] == "archived" else "assigned",
        "summary": x["summary"],
        "reply_draft": None,
        "run_id": ctx.run_id,
    }
    lead["sla_hours"] = sla_hours  # transient, used by the reply prompt
    ctx.data["lead"] = lead
    return {"queue": queue, "owner": owner, "sla_hours": sla_hours, "sla_due_at": lead["sla_due_at"]}


def step_ai_reply(ctx: Context) -> dict:
    lead = ctx.data["lead"]
    if lead["tier"] == "archived":
        lead["reply_draft"] = None
        return {"skipped": True, "reason": "archived as spam"}
    result = ctx.ai.draft_reply(lead)
    ctx.account(result)
    lead["reply_draft"] = result.data["reply"]
    return {"mode": result.mode, "latency_ms": result.latency_ms, "chars": len(result.data["reply"])}


def step_deliver(ctx: Context) -> dict:
    lead = dict(ctx.data["lead"])
    lead.pop("sla_hours", None)
    db.save_lead(lead)
    ctx.data["lead"] = lead

    crm = integrations.crm_upsert(
        {
            "id": lead["id"], "name": lead["name"], "email": lead["email"],
            "phone": lead["phone"], "company": lead["company"], "lifecycle_stage": lead["tier"],
            "lead_score": lead["score"], "owner": lead["owner"], "source": lead["channel"],
            "notes": lead["summary"],
        },
        ctx.run_id,
    )
    note = integrations.notify(lead, ctx.run_id)
    event = integrations.fan_out(
        {"type": "lead.qualified", "run_id": ctx.run_id, "lead": lead}, ctx.run_id
    )

    outcomes = {"crm": crm.ok, "notify": note.ok, "outbound_webhook": event.ok}
    if not all(outcomes.values()):
        failed = [k for k, ok in outcomes.items() if not ok]
        raise WorkflowError(f"delivery failed for: {', '.join(failed)}")
    return {
        "lead_id": lead["id"],
        "crm": {"ok": crm.ok, "simulated": crm.get("simulated"), "status": crm.get("status_code")},
        "notify": {"ok": note.ok, "simulated": note.get("simulated")},
        "outbound_webhook": {"ok": event.ok, "simulated": event.get("simulated")},
    }


LEAD_TRIAGE = Workflow(
    "inbound_lead_triage",
    [
        Step("ingest",     "Ingest & normalise",       step_ingest),
        Step("ai_extract", "AI extraction (Claude)",   step_ai_extract, retries=1),
        Step("enrich",     "Company enrichment (REST)", step_enrich, retries=1, optional=True),
        Step("score",      "Lead scoring (rules)",     step_score),
        Step("route",      "Routing & SLA",            step_route),
        Step("ai_reply",   "AI reply draft (Claude)",  step_ai_reply, retries=1, optional=True),
        Step("deliver",    "CRM + notify + webhook",   step_deliver, retries=1, optional=True),
    ],
)


def replay(run_id: str) -> dict | None:
    """Re-run a stored payload through the current workflow definition."""
    import json

    row = db.query_one("SELECT payload_json, source, verified FROM runs WHERE id = ?", (run_id,))
    if not row or not row["payload_json"]:
        return None
    return LEAD_TRIAGE.run(
        json.loads(row["payload_json"]), source=f"replay:{row['source']}", verified=bool(row["verified"])
    )
