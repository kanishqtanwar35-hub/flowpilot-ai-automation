"""The API key must not be reachable through any output the app produces.

Note on the fixtures below: every fake token is **assembled at runtime from
fragments**, never written as a literal. A literal `xoxb-…` or `sk-ant-…` in a
committed file is exactly what GitHub's push protection blocks — correctly, since
it cannot know ours is fake. Concatenating the parts keeps this suite honest
(the assembled value is a real token *shape*, which is what we are testing) while
leaving nothing in the source that any scanner should ever flag.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app import db
from app.ai import AIEngine
from app.security import MASK, redact, safe_url
from app.workflow import LEAD_TRIAGE


def fake(prefix: str, body: str) -> str:
    """Assemble a token-shaped string at runtime. See the module docstring."""
    return prefix + body


FAKE_KEY = fake("sk-" + "ant-", "api03-ThisIsNotARealKeyButLooksLikeOne1234567890")
FAKE_SLACK_TOKEN = fake("xo" + "xb-", "12345678-abcdefghijklmno")
FAKE_GH_TOKEN = fake("gh" + "p_", "abcdefghijklmnopqrstuvwxyz0123")
FAKE_WHSEC = fake("wh" + "sec_", "abcdefghijklmnopqrstuvwxyz012345")
FAKE_BEARER = fake("Authorization: Bea" + "rer ", "abcdefghijklmnop")
FAKE_SLACK_URL = fake("https://hooks.slack.com/services/", "T01ABCD/B02EFGH/xxxxSECRETxxxx")


def test_known_token_shapes_are_redacted():
    for token in (FAKE_KEY, FAKE_SLACK_TOKEN, FAKE_GH_TOKEN, FAKE_WHSEC, FAKE_BEARER, FAKE_SLACK_URL):
        assert token not in redact(f"boom while calling api ({token}) retrying")


def test_the_configured_key_is_redacted_even_in_an_unknown_shape(monkeypatch):
    """A key that matches no known pattern is still caught, because we know its value."""
    from app import config

    monkeypatch.setattr(config.Config, "ANTHROPIC_API_KEY", "totally-custom-secret-value")
    assert "totally-custom-secret-value" not in redact("key=totally-custom-secret-value in url")


def test_redaction_walks_nested_structures():
    cleaned = redact({"steps": [{"note": f"failed with {FAKE_KEY}"}], "n": 3})
    assert cleaned["steps"][0]["note"].endswith(MASK)
    assert cleaned["n"] == 3


def test_ordinary_text_survives_redaction():
    text = "Budget around $15,000, need it live in 4 weeks. Reach me at sana@vertexfoods.com"
    assert redact(text) == text


def test_documentation_is_not_mistaken_for_a_leak():
    """Prose and f-strings must not trip the scanner, or nobody will trust it."""
    for benign in (
        "requires `Authorization: Bearer <key>`",
        'headers = {"Authorization": f"Bearer {api_key}"}',
        "stored as https://hooks.slack.com/services/…",
    ):
        assert redact(benign) == benign


def test_safe_url_keeps_the_host_but_drops_the_credential():
    out = safe_url(FAKE_SLACK_URL)
    assert "SECRET" not in out
    assert out.startswith("https://hooks.slack.com/services")

    assert safe_url("https://api.crm.io/v1/contacts?token=abcdefghijklmnop") == "https://api.crm.io/v1/contacts?…"
    assert safe_url("https://user:pass@internal.example.com/hook") == "https://internal.example.com/hook"
    assert safe_url(None) is None


def test_a_leaking_sdk_error_never_reaches_the_stored_run():
    """The realistic leak: an SDK exception quoting the request, saved into the trace."""
    class Leaky:
        def create(self, **_):
            raise RuntimeError(f"401 unauthorized for request with x-api-key: {FAKE_KEY}")

    engine = AIEngine(api_key=FAKE_KEY)
    engine._client = SimpleNamespace(messages=Leaky())

    result = engine.extract("We need automation, budget $9,000.", "api", {})
    assert result.mode == "heuristic-fallback"
    assert FAKE_KEY not in (result.note or "")
    assert MASK in result.note


def test_no_secret_survives_into_the_database_or_the_api(client, monkeypatch):
    import app.workflow as wf

    def leaky_step(_ctx):
        raise RuntimeError(f"CRM rejected us: {FAKE_BEARER}{FAKE_KEY}")

    monkeypatch.setattr(wf.LEAD_TRIAGE.steps[-1], "fn", leaky_step)
    run = LEAD_TRIAGE.run(
        {"channel": "api", "email": "a@vertexfoods.com", "message": "Need an automation build."},
        source="test",
    )

    stored = db.query_one("SELECT * FROM run_steps WHERE run_id = ? AND name = 'deliver'", (run["id"],))
    assert FAKE_KEY not in json.dumps(stored)

    body = client.get("/api/runs/" + run["id"]).get_data(as_text=True)
    assert FAKE_KEY not in body
    assert MASK in body


def test_health_endpoint_reports_modes_not_values(client):
    body = client.get("/api/health").get_data(as_text=True)
    assert "heuristic" in body
    for word in ("sk-ant", "api_key", "secret", "ANTHROPIC"):
        assert word not in body
