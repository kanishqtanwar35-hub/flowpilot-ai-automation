"""JSON API that backs the dashboard (deliverable #3)."""
from __future__ import annotations

import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

from . import db
from .config import Config
from .workflow import LEAD_TRIAGE, replay

bp = Blueprint("api", __name__, url_prefix="/api")

TIERS = ["hot", "warm", "cold", "archived"]


def _days_arg(default: int = 14) -> int:
    try:
        return max(1, min(90, int(request.args.get("days", default))))
    except ValueError:
        return default


@bp.get("/health")
def health():
    return jsonify({"ok": True, "config": Config.summary(), "time": datetime.now(timezone.utc).isoformat()})


@bp.get("/stats")
def stats():
    days = _days_arg()
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()

    totals = db.query_one(
        """SELECT COUNT(*) AS leads,
                  COALESCE(AVG(score), 0) AS avg_score,
                  SUM(CASE WHEN tier = 'hot' THEN 1 ELSE 0 END) AS hot,
                  SUM(CASE WHEN tier = 'archived' THEN 1 ELSE 0 END) AS archived
             FROM leads WHERE date(created_at) >= ?""",
        (since,),
    ) or {}

    runs = db.query_one(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial,
                  SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) AS failed,
                  COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cost_usd), 0) AS cost_usd
             FROM runs WHERE date(started_at) >= ?""",
        (since,),
    ) or {}

    total_runs = runs.get("total") or 0
    handled = (runs.get("success") or 0) + (runs.get("partial") or 0)

    # daily series: volume vs. quality, plus the automation split for the table view
    runs_by_day = {
        r["day"]: r
        for r in db.query(
            """SELECT date(started_at) AS day,
                      COUNT(*) AS runs,
                      SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS clean
                 FROM runs WHERE date(started_at) >= ? GROUP BY day""",
            (since,),
        )
    }
    leads_by_day = {
        r["day"]: r
        for r in db.query(
            """SELECT date(created_at) AS day,
                      COUNT(*) AS leads,
                      SUM(CASE WHEN tier = 'hot' THEN 1 ELSE 0 END) AS hot
                 FROM leads WHERE date(created_at) >= ? GROUP BY day""",
            (since,),
        )
    }
    series = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).date().isoformat()
        r, l = runs_by_day.get(d, {}), leads_by_day.get(d, {})
        series.append(
            {
                "day": d,
                "runs": r.get("runs", 0),
                "clean": r.get("clean", 0),
                "leads": l.get("leads", 0),
                "hot": l.get("hot", 0),
            }
        )

    def group(column: str, table: str = "leads", limit: int = 8):
        rows = db.query(
            f"""SELECT COALESCE({column}, 'unknown') AS key, COUNT(*) AS value
                  FROM {table} WHERE date(created_at) >= ?
                 GROUP BY key ORDER BY value DESC LIMIT {limit}""",
            (since,),
        )
        return [{"key": r["key"], "value": r["value"]} for r in rows]

    tier_counts = {t: 0 for t in TIERS}
    for row in db.query(
        "SELECT tier, COUNT(*) AS n FROM leads WHERE date(created_at) >= ? GROUP BY tier", (since,)
    ):
        tier_counts[row["tier"] or "cold"] = row["n"]

    integrations = db.query(
        """SELECT target,
                  COUNT(*) AS calls,
                  SUM(ok) AS ok,
                  SUM(simulated) AS simulated,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
             FROM deliveries WHERE date(created_at) >= ? GROUP BY target ORDER BY target""",
        (since,),
    )

    sla_at_risk = db.scalar(
        """SELECT COUNT(*) FROM leads
            WHERE status = 'assigned' AND sla_due_at IS NOT NULL AND sla_due_at < ?""",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )

    return jsonify(
        {
            "range_days": days,
            "kpis": {
                "leads": totals.get("leads", 0),
                "hot_leads": totals.get("hot", 0) or 0,
                "avg_score": round(totals.get("avg_score", 0) or 0, 1),
                "spam_filtered": totals.get("archived", 0) or 0,
                "automation_rate": round(handled / total_runs * 100, 1) if total_runs else 0.0,
                "avg_duration_ms": int(runs.get("avg_duration_ms") or 0),
                "runs": total_runs,
                "failed_runs": runs.get("failed") or 0,
                "sla_at_risk": sla_at_risk,
                "tokens": (runs.get("input_tokens") or 0) + (runs.get("output_tokens") or 0),
                "cost_usd": round(runs.get("cost_usd") or 0, 4),
            },
            "series": series,
            "by_channel": group("channel"),
            "by_intent": group("intent"),
            "by_tier": [{"key": t, "value": tier_counts[t]} for t in TIERS],
            "integrations": integrations,
            "config": Config.summary(),
        }
    )


@bp.get("/leads")
def leads():
    where, params = ["1=1"], []
    if tier := request.args.get("tier"):
        where.append("tier = ?")
        params.append(tier)
    if channel := request.args.get("channel"):
        where.append("channel = ?")
        params.append(channel)
    if q := request.args.get("q"):
        where.append("(name LIKE ? OR email LIKE ? OR company LIKE ? OR message LIKE ?)")
        params += [f"%{q}%"] * 4
    limit = min(200, int(request.args.get("limit", 50) or 50))

    rows = db.query(
        f"""SELECT id, created_at, channel, name, email, company, intent, category, urgency,
                   sentiment, score, tier, owner, queue, sla_due_at, status, summary, run_id
              FROM leads WHERE {' AND '.join(where)}
             ORDER BY created_at DESC LIMIT {limit}""",
        params,
    )
    return jsonify({"count": len(rows), "leads": rows})


@bp.get("/leads/<lead_id>")
def lead_detail(lead_id: str):
    lead = db.query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        return jsonify({"ok": False, "error": "not found"}), 404
    lead["run"] = _run_payload(lead["run_id"]) if lead.get("run_id") else None
    return jsonify(lead)


@bp.get("/runs")
def runs():
    limit = min(200, int(request.args.get("limit", 40) or 40))
    rows = db.query(
        """SELECT id, workflow, source, status, verified, started_at, duration_ms,
                  ai_mode, cost_usd, lead_id, error
             FROM runs ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    )
    return jsonify({"count": len(rows), "runs": rows})


def _run_payload(run_id: str) -> dict | None:
    run = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run:
        return None
    run["payload"] = json.loads(run.pop("payload_json") or "null")
    run["result"] = json.loads(run.pop("result_json") or "null")
    steps = db.query("SELECT * FROM run_steps WHERE run_id = ? ORDER BY idx", (run_id,))
    for step in steps:
        step["output"] = json.loads(step.pop("output_json") or "null")
    run["steps"] = steps
    run["deliveries"] = db.query(
        "SELECT target, ok, simulated, status_code, latency_ms, created_at FROM deliveries WHERE run_id = ?",
        (run_id,),
    )
    return run


@bp.get("/runs/<run_id>")
def run_detail(run_id: str):
    run = _run_payload(run_id)
    if not run:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(run)


@bp.post("/runs/<run_id>/replay")
def run_replay(run_id: str):
    run = replay(run_id)
    if not run:
        return jsonify({"ok": False, "error": "no stored payload for that run"}), 404
    return jsonify({"ok": True, "run_id": run["id"], "status": run["status"]})


# --------------------------------------------------------------------------- #
# Demo helpers — used by the dashboard's "Simulate inbound" button
# --------------------------------------------------------------------------- #
SAMPLES = [
    {
        "channel": "web_form", "name": "Priya Sharma", "email": "priya@northwind-retail.com",
        "message": "Hi, we run 14 retail stores and want to automate WhatsApp order updates "
                   "and a support chatbot on our site. Budget is around ₹4,00,000 and we'd "
                   "like to start within 3 weeks. Can we get a demo this week?",
    },
    {
        "channel": "whatsapp", "name": "Arun", "phone": "+91 98765 43210",
        "message": "URGENT - the invoice sync between our CRM and Tally has been failing since "
                   "yesterday, nothing is coming through. This is blocking month-end close.",
    },
    {
        "channel": "email", "name": "Daniel Okafor", "email": "daniel@brightpath.io",
        "message": "Following up on pricing for the AI dashboard build. How much would a "
                   "3-month engagement cost, and what does support look like after handover?",
    },
    {
        "channel": "chat_widget", "name": "", "email": "seo.master.999@gmail.com",
        "message": "Hello sir, we provide 5000 high quality backlinks and guaranteed page 1 "
                   "ranking, very cheap price. Reply for package list.",
    },
    {
        "channel": "web_form", "name": "Meera Iyer", "email": "meera@lumenhealth.in",
        "message": "We're exploring an AI agent to triage patient enquiries. No rush — planning "
                   "for next quarter. Could you share case studies from healthcare?",
    },
    {
        "channel": "email", "name": "Tom Blake", "email": "tom@blake-logistics.co.uk",
        "message": "Your automation quote looks good but I'm frustrated that our last two "
                   "emails went unanswered. We need a decision this week or we go elsewhere.",
    },
]


@bp.post("/demo/simulate")
def demo_simulate():
    body = request.get_json(silent=True) or {}
    payload = body.get("payload") or random.choice(SAMPLES)
    run = LEAD_TRIAGE.run(payload, source="dashboard:simulate", verified=False)
    return jsonify({"ok": run["status"] != "failed", "run_id": run["id"], "status": run["status"]})


@bp.get("/export.csv")
def export_csv():
    rows = db.query(
        """SELECT created_at, channel, name, email, company, intent, category, urgency,
                  sentiment, score, tier, owner, queue, sla_due_at, status, summary
             FROM leads ORDER BY created_at DESC"""
    )
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=list(rows[0].keys()) if rows else ["created_at", "channel", "name", "score", "tier"],
    )
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=flowpilot-leads.csv"},
    )
