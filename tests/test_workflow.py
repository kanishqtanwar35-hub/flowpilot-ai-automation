from app import db
from app.workflow import LEAD_TRIAGE, replay


def test_qualified_lead_runs_every_step_and_persists():
    run = LEAD_TRIAGE.run(
        {
            "channel": "web_form",
            "name": "Priya Sharma",
            "email": "priya@northwind-retail.com",
            "message": "We need to automate WhatsApp order updates and a support chatbot. "
                       "Budget is around ₹4,00,000 and we want to start within 3 weeks. Urgent.",
        },
        source="test",
        verified=True,
    )

    assert run["status"] == "success"
    assert [s["name"] for s in run["steps"]] == [
        "ingest", "ai_extract", "enrich", "score", "route", "ai_reply", "deliver"
    ]
    assert all(s["status"] == "success" for s in run["steps"])

    lead = run["result"]
    assert lead["intent"] == "new_business"
    assert lead["urgency"] == "critical"          # "urgent" in the message
    assert lead["tier"] == "hot"                  # business email + budget + timeline
    assert lead["score"] >= 70
    assert lead["owner"] and lead["queue"] == "Sales"
    assert lead["reply_draft"]

    stored = db.query_one("SELECT * FROM leads WHERE id = ?", (lead["id"],))
    assert stored is not None and stored["run_id"] == run["id"]


def test_spam_is_archived_and_never_routed_to_sales():
    run = LEAD_TRIAGE.run(
        {
            "channel": "chat_widget",
            "email": "seo.master.999@gmail.com",
            "message": "We provide 5000 high quality backlinks and guaranteed page 1 ranking, cheap price.",
        },
        source="test",
    )
    lead = run["result"]
    assert lead["tier"] == "archived"
    assert lead["queue"] == "Archive"
    assert lead["status"] == "archived"
    assert lead["reply_draft"] is None            # no auto-reply to spam


def test_support_issue_routes_to_support_queue_with_tight_sla():
    run = LEAD_TRIAGE.run(
        {
            "channel": "whatsapp",
            "email": "ops@blake-logistics.co.uk",
            "message": "URGENT - the invoice sync is failing since yesterday, nothing is coming through.",
        },
        source="test",
    )
    lead = run["result"]
    assert lead["intent"] == "support_issue"
    assert lead["queue"] == "Support"
    assert lead["sla_due_at"] > lead["created_at"]


def test_empty_message_fails_fast_at_ingest():
    run = LEAD_TRIAGE.run({"channel": "api", "message": "   "}, source="test")
    assert run["status"] == "failed"
    assert len(run["steps"]) == 1
    assert run["steps"][0]["name"] == "ingest"
    assert "no message" in run["error"]


def test_scoring_is_deterministic_across_replays():
    payload = {
        "channel": "email",
        "email": "tom@vertexfoods.com",
        "message": "Looking for a quote to automate lead routing into HubSpot. Budget $12,000, next month.",
    }
    first = LEAD_TRIAGE.run(payload, source="test")
    second = replay(first["id"])

    assert second is not None
    assert second["result"]["score"] == first["result"]["score"]
    assert second["result"]["tier"] == first["result"]["tier"]
    assert second["id"] != first["id"]           # replay creates a new, traceable run


def test_step_failure_is_recorded_with_attempts(monkeypatch):
    import app.workflow as wf

    calls = {"n": 0}

    def boom(_ctx):
        calls["n"] += 1
        raise wf.WorkflowError("simulated CRM outage")

    monkeypatch.setattr(wf.LEAD_TRIAGE.steps[-1], "fn", boom)
    run = wf.LEAD_TRIAGE.run(
        {"channel": "api", "email": "a@vertexfoods.com", "message": "Need an automation build, budget $9,000."},
        source="test",
    )

    deliver = run["steps"][-1]
    assert calls["n"] == 2                        # one retry, as configured
    assert deliver["attempts"] == 2
    assert deliver["status"] == "skipped"         # optional step -> run degrades, lead is not lost
    assert run["status"] == "partial"
