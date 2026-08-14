from app.ai import AIEngine


def test_heuristic_extraction_pulls_the_fields_scoring_depends_on():
    engine = AIEngine(api_key="")
    result = engine.extract(
        "Hi, we need to automate our WhatsApp order updates. Budget around ₹4,00,000 and we want "
        "to go live within 3 weeks. Reach me on priya@northwind-retail.com or +91 98765 43210.",
        channel="web_form",
        meta={},
    )

    assert result.mode == "heuristic"
    data = result.data
    assert data["intent"] == "new_business"
    assert data["category"] == "ai_automation"
    assert data["email"] == "priya@northwind-retail.com"
    assert data["phone"]
    assert data["budget"] and data["timeline"]
    assert data["company"] == "Northwind Retail"      # derived from a business domain
    assert 0.0 <= data["spam_score"] <= 1.0


def test_free_mail_domains_do_not_become_company_names():
    engine = AIEngine(api_key="")
    data = engine.extract("Can you send pricing?", "email", {"email": "someone@gmail.com"}).data
    assert data["company"] is None


def test_normalise_clamps_anything_the_model_might_return():
    cleaned = AIEngine._normalise(
        {
            "intent": "definitely_not_an_intent",
            "category": None,
            "urgency": "EXTREME",
            "sentiment": 42,
            "spam_score": 9.5,
            "person_name": "   ",
            "summary": "",
            "key_points": "not a list",
        }
    )
    assert cleaned["intent"] == "other"
    assert cleaned["category"] == "general"
    assert cleaned["urgency"] == "normal"
    assert cleaned["sentiment"] == "neutral"
    assert cleaned["spam_score"] == 1.0
    assert cleaned["person_name"] is None
    assert cleaned["summary"] == "No summary available."
    assert cleaned["key_points"] == []


def test_stats_endpoint_shape(client):
    client.post("/api/demo/simulate", json={})
    body = client.get("/api/stats?days=7").get_json()

    assert body["range_days"] == 7
    assert len(body["series"]) == 7
    for key in ("leads", "hot_leads", "automation_rate", "avg_duration_ms", "cost_usd"):
        assert key in body["kpis"]
    assert {t["key"] for t in body["by_tier"]} == {"hot", "warm", "cold", "archived"}


def test_leads_and_run_detail_endpoints(client):
    sim = client.post("/api/demo/simulate", json={}).get_json()
    assert sim["ok"] is True

    leads = client.get("/api/leads?limit=5").get_json()
    assert leads["count"] >= 1

    run = client.get("/api/runs/" + sim["run_id"]).get_json()
    assert run["id"] == sim["run_id"]
    assert run["steps"] and run["steps"][0]["name"] == "ingest"
    assert run["deliveries"]


def test_csv_export_has_a_header_row(client):
    client.post("/api/demo/simulate", json={})
    res = client.get("/api/export.csv")
    assert res.status_code == 200
    assert res.headers["Content-Disposition"].endswith("flowpilot-leads.csv")
    assert res.data.decode().splitlines()[0].startswith("created_at,channel")
