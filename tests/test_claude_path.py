"""Exercise the Claude code path with a stub SDK client.

No API key is required: we assert on the request we build and on how we parse
the response, then prove the automatic degradation to the rule engine.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai import EXTRACTION_SCHEMA, AIEngine

VALID = {
    "intent": "new_business", "category": "ai_automation", "urgency": "high",
    "sentiment": "positive", "spam_score": 0.01, "person_name": "Priya Sharma",
    "email": "priya@northwind-retail.com", "phone": None, "company": "Northwind Retail",
    "budget": "₹4,00,000", "timeline": "3 weeks",
    "summary": "Wants WhatsApp order automation and a support chatbot within 3 weeks.",
    "key_points": ["14 retail outlets", "budget stated"],
}


class StubMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.payload)],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=820, output_tokens=140, cache_read_input_tokens=0),
        )


def engine_with(payload, stop_reason="end_turn"):
    engine = AIEngine(api_key="sk-test-key", model="claude-opus-5")
    stub = StubMessages(payload, stop_reason)
    engine._client = SimpleNamespace(messages=stub)
    return engine, stub


def test_extraction_request_uses_the_strict_schema_and_costs_are_accounted():
    engine, stub = engine_with(json.dumps(VALID))
    result = engine.extract("We need WhatsApp automation.", "web_form", {"name": "Priya Sharma"})

    sent = stub.calls[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": EXTRACTION_SCHEMA}
    assert sent["output_config"]["effort"] in {"low", "medium", "high", "xhigh", "max"}
    body = json.loads(sent["messages"][0]["content"])
    assert body["channel"] == "web_form" and body["form_fields"]["name"] == "Priya Sharma"

    assert result.mode == "claude"
    assert result.data["company"] == "Northwind Retail"
    assert result.input_tokens == 820 and result.output_tokens == 140
    # 820 in @ $5/MTok = $0.0041, 140 out @ $25/MTok = $0.0035
    assert result.cost_usd == pytest.approx(0.0041 + 0.0035, rel=1e-6)


def test_schema_is_strict_enough_for_structured_outputs():
    assert EXTRACTION_SCHEMA["additionalProperties"] is False
    assert set(EXTRACTION_SCHEMA["required"]) == set(EXTRACTION_SCHEMA["properties"])


def test_model_output_is_still_normalised():
    rogue = {**VALID, "intent": "made_up_intent", "spam_score": 7}
    engine, _ = engine_with(json.dumps(rogue))
    data = engine.extract("hello", "api", {}).data
    assert data["intent"] == "other" and data["spam_score"] == 1.0


def test_a_refusal_degrades_to_the_rule_engine():
    engine, _ = engine_with("", stop_reason="refusal")
    result = engine.extract("We need automation, budget $9,000.", "api", {})
    assert result.mode == "heuristic-fallback"
    assert result.note and "claude_error" in result.note
    assert result.data["intent"] == "new_business"      # the run still produces a usable lead


def test_transport_failure_degrades_to_the_rule_engine():
    class Boom:
        def create(self, **_):
            raise ConnectionError("connection reset")

    engine = AIEngine(api_key="sk-test-key")
    engine._client = SimpleNamespace(messages=Boom())
    result = engine.extract("Pricing please", "email", {})
    assert result.mode == "heuristic-fallback"
    assert result.data["intent"] == "pricing_question"


def test_reply_drafting_falls_back_to_the_template():
    class Boom:
        def create(self, **_):
            raise TimeoutError("read timeout")

    engine = AIEngine(api_key="sk-test-key")
    engine._client = SimpleNamespace(messages=Boom())
    out = engine.draft_reply({"name": "Priya Sharma", "owner": "Aditi Rao", "sla_hours": 2,
                              "intent": "new_business", "category": "ai_automation"})
    assert out.mode == "heuristic-fallback"
    assert "Priya" in out.data["reply"] and "Aditi Rao" in out.data["reply"]
