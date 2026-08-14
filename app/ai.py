"""AI layer.

Two interchangeable engines behind one interface:

* ``ClaudeEngine``    — Claude (Anthropic SDK) with a strict JSON schema, so the
                        extraction result is guaranteed parseable.
* ``HeuristicEngine`` — deterministic rules/regex. Used when no API key is set,
                        and as the automatic fallback if the API call fails.

The workflow never cares which one answered; it reads ``result.mode`` for the
dashboard badge and moves on. That is what keeps the demo runnable offline.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .security import redact

log = logging.getLogger("flowpilot.ai")

INTENTS = [
    "new_business",      # wants to buy / start a project
    "support_issue",     # existing customer with a problem
    "pricing_question",
    "partnership",
    "recruitment",
    "spam",
    "other",
]
CATEGORIES = [
    "ai_automation", "web_development", "mobile_app", "data_analytics",
    "integration", "billing", "bug_report", "general",
]
URGENCIES = ["critical", "high", "normal", "low"]
SENTIMENTS = ["positive", "neutral", "negative"]

# Strict JSON Schema -> `output_config.format`. `additionalProperties: false`
# plus a full `required` list is mandatory for structured outputs.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "category": {"type": "string", "enum": CATEGORIES},
        "urgency": {"type": "string", "enum": URGENCIES},
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "spam_score": {"type": "number", "description": "0.0 = genuine, 1.0 = certain spam"},
        "person_name": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"]},
        "company": {"type": ["string", "null"]},
        "budget": {"type": ["string", "null"], "description": "Verbatim budget if stated, else null"},
        "timeline": {"type": ["string", "null"], "description": "Verbatim timeline if stated, else null"},
        "summary": {"type": "string", "description": "One sentence, max 25 words, for the triage queue"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "intent", "category", "urgency", "sentiment", "spam_score", "person_name",
        "email", "phone", "company", "budget", "timeline", "summary", "key_points",
    ],
    "additionalProperties": False,
}

EXTRACTION_SYSTEM = """You triage inbound messages for an AI-automation agency.

Extract only what the message actually states. Never invent a name, company,
budget or timeline — return null when it is not present. `summary` is written for
a human on the triage queue: what they want, and anything time-sensitive.
`spam_score` is high for bulk SEO/link-building/crypto pitches and mass mailers."""

REPLY_SYSTEM = """You write the first reply to an inbound enquiry, as an account
manager at an AI-automation agency.

Rules:
- Plain text, no markdown, no subject line. 90 words maximum.
- Open by acknowledging their specific ask in their own words.
- Give one concrete next step with a named time window.
- Never promise pricing, delivery dates, or capabilities that were not asked about.
- If the message is a support issue, lead with the fix or the escalation, not sales.
- Sign off as "Kanishq — FlowPilot Automation"."""

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,13}\d")
FREE_MAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "proton.me", "protonmail.com", "aol.com", "rediffmail.com", "mail.com",
}


@dataclass
class AIResult:
    data: dict
    mode: str                       # "claude" | "heuristic" | "heuristic-fallback"
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    note: str | None = None
    raw_stop_reason: str | None = None


@dataclass
class AIEngine:
    """Facade that picks Claude when configured and degrades gracefully."""

    api_key: str = field(default_factory=lambda: Config.ANTHROPIC_API_KEY)
    model: str = field(default_factory=lambda: Config.AI_MODEL)
    _client: Any = field(default=None, init=False, repr=False)

    # -- public API ---------------------------------------------------------
    @property
    def mode(self) -> str:
        return "claude" if self.api_key else "heuristic"

    def extract(self, message: str, channel: str, meta: dict | None = None) -> AIResult:
        if not self.api_key:
            return self._heuristic_extract(message, meta or {}, mode="heuristic")
        try:
            return self._claude_extract(message, channel, meta or {})
        except Exception as exc:  # noqa: BLE001 - any failure must degrade, not crash
            log.warning("Claude extraction failed (%s); falling back to rules", exc)
            result = self._heuristic_extract(message, meta or {}, mode="heuristic-fallback")
            # SDK errors can quote the request/response — redact before it is stored or shown
            result.note = redact(f"claude_error: {type(exc).__name__}: {exc}")[:300]
            return result

    def draft_reply(self, lead: dict) -> AIResult:
        if not self.api_key:
            return AIResult(data={"reply": self._template_reply(lead)}, mode="heuristic")
        try:
            return self._claude_reply(lead)
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude reply failed (%s); using template", exc)
            return AIResult(
                data={"reply": self._template_reply(lead)},
                mode="heuristic-fallback",
                note=redact(f"claude_error: {type(exc).__name__}"),
            )

    # -- Claude -------------------------------------------------------------
    def _client_or_create(self):
        if self._client is None:
            import anthropic  # imported lazily so the app boots without the SDK

            # The SDK already retries 408/409/429/5xx with exponential backoff.
            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=3, timeout=30.0)
        return self._client

    def _claude_extract(self, message: str, channel: str, meta: dict) -> AIResult:
        client = self._client_or_create()
        started = time.perf_counter()
        user_block = json.dumps(
            {"channel": channel, "message": message, "form_fields": meta}, ensure_ascii=False
        )
        response = client.messages.create(
            model=self.model,
            max_tokens=Config.AI_MAX_TOKENS,
            # Both prompts sit below the prompt-cache minimum, so no cache_control:
            # a marker on a sub-minimum prefix silently never caches.
            system=EXTRACTION_SYSTEM,
            output_config={
                "effort": Config.AI_EFFORT,
                "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            },
            messages=[{"role": "user", "content": user_block}],
        )
        latency = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            raise RuntimeError("model refused the request")

        text = next((b.text for b in response.content if b.type == "text"), "")
        data = self._normalise(json.loads(text))
        return AIResult(
            data=data,
            mode="claude",
            latency_ms=latency,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=self._cost(response.usage),
            raw_stop_reason=response.stop_reason,
        )

    def _claude_reply(self, lead: dict) -> AIResult:
        client = self._client_or_create()
        started = time.perf_counter()
        brief = json.dumps(
            {
                "name": lead.get("name"),
                "company": lead.get("company"),
                "channel": lead.get("channel"),
                "intent": lead.get("intent"),
                "category": lead.get("category"),
                "urgency": lead.get("urgency"),
                "tier": lead.get("tier"),
                "owner": lead.get("owner"),
                "sla_hours": lead.get("sla_hours"),
                "their_message": lead.get("message"),
            },
            ensure_ascii=False,
        )
        response = client.messages.create(
            model=self.model,
            max_tokens=Config.AI_MAX_TOKENS,
            system=REPLY_SYSTEM,
            output_config={"effort": Config.AI_EFFORT},
            messages=[{"role": "user", "content": brief}],
        )
        latency = int((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        reply = "\n".join(b.text for b in response.content if b.type == "text").strip()
        return AIResult(
            data={"reply": reply or self._template_reply(lead)},
            mode="claude",
            latency_ms=latency,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=self._cost(response.usage),
            raw_stop_reason=response.stop_reason,
        )

    @staticmethod
    def _cost(usage) -> float:
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        billed_in = usage.input_tokens + cached * 0.1
        return round(
            billed_in / 1_000_000 * Config.AI_PRICE_IN
            + usage.output_tokens / 1_000_000 * Config.AI_PRICE_OUT,
            6,
        )

    # -- heuristic engine ---------------------------------------------------
    def _heuristic_extract(self, message: str, meta: dict, mode: str) -> AIResult:
        started = time.perf_counter()
        text = (message or "").strip()
        low = text.lower()

        def any_of(*words: str) -> bool:
            return any(w in low for w in words)

        if any_of("seo service", "backlink", "guest post", "crypto", "forex", "unsubscribe",
                  "increase your ranking", "bulk sms", "cheap traffic"):
            intent, spam = "spam", 0.92
        elif any_of("broken", "not working", "error", "bug", "down", "failing", "crash", "issue with"):
            intent, spam = "support_issue", 0.02
        elif any_of("how much", "pricing", "price", "quote", "cost of", "rate card"):
            intent, spam = "pricing_question", 0.03
        elif any_of("partner", "reseller", "white label", "collaborat"):
            intent, spam = "partnership", 0.05
        elif any_of("resume", "cv", "job", "internship", "hiring", "vacancy"):
            intent, spam = "recruitment", 0.05
        elif any_of("build", "automate", "need", "looking for", "project", "integrate", "demo", "chatbot"):
            intent, spam = "new_business", 0.02
        else:
            intent, spam = "other", 0.10

        if any_of("chatbot", "ai agent", "automat", "workflow", "gpt", "llm", "whatsapp", "voice bot"):
            category = "ai_automation"
        elif any_of("api", "integrat", "webhook", "sync", "crm"):
            category = "integration"
        elif any_of("dashboard", "report", "analytics", "data"):
            category = "data_analytics"
        elif any_of("android", "ios", "mobile app", "flutter"):
            category = "mobile_app"
        elif any_of("website", "web app", "landing page", "frontend"):
            category = "web_development"
        elif any_of("invoice", "billing", "payment", "refund"):
            category = "billing"
        elif intent == "support_issue":
            category = "bug_report"
        else:
            category = "general"

        if any_of("urgent", "asap", "immediately", "today", "production is down", "critical"):
            urgency = "critical"
        elif any_of("this week", "soon", "quickly", "priority", "deadline"):
            urgency = "high"
        elif any_of("no rush", "whenever", "next quarter", "exploring"):
            urgency = "low"
        else:
            urgency = "normal"

        if any_of("frustrat", "unacceptable", "angry", "terrible", "disappoint", "worst", "still not"):
            sentiment = "negative"
        elif any_of("thanks", "great", "love", "excited", "impressed", "appreciate"):
            sentiment = "positive"
        else:
            sentiment = "neutral"

        email = meta.get("email") or (EMAIL_RE.search(text).group(0) if EMAIL_RE.search(text) else None)
        phone_match = PHONE_RE.search(text.replace("\n", " "))
        phone = meta.get("phone") or (phone_match.group(0).strip() if phone_match else None)

        company = meta.get("company")
        if not company and email:
            domain = email.split("@")[-1].lower()
            if domain not in FREE_MAIL:
                company = domain.split(".")[0].replace("-", " ").title()

        budget = self._first_match(
            text, r"(?:budget|approx\w*|around|upto|up to)?\s*(?:₹|rs\.?|inr|\$|usd|€)\s?[\d,]+(?:\s?(?:k|lakh|lakhs|cr|crore|million|m))?"
        )
        timeline = self._first_match(
            text, r"(?:in|within|by|before)\s+(?:the\s+)?(?:next\s+)?\d+\s*(?:day|days|week|weeks|month|months)|next (?:week|month|quarter)|end of (?:the )?(?:month|quarter|year)|asap"
        )

        summary = re.sub(r"\s+", " ", text)[:160]
        if len(text) > 160:
            summary = summary.rsplit(" ", 1)[0] + "…"

        data = {
            "intent": intent,
            "category": category,
            "urgency": urgency,
            "sentiment": sentiment,
            "spam_score": spam,
            "person_name": meta.get("name"),
            "email": email,
            "phone": phone,
            "company": company,
            "budget": budget,
            "timeline": timeline,
            "summary": summary or "(empty message)",
            "key_points": [s.strip() for s in re.split(r"[.\n]", text) if len(s.strip()) > 15][:3],
        }
        return AIResult(
            data=self._normalise(data),
            mode=mode,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _first_match(text: str, pattern: str) -> str | None:
        m = re.search(pattern, text, re.I)
        return m.group(0).strip() if m else None

    @staticmethod
    def _template_reply(lead: dict) -> str:
        name = (lead.get("name") or "there").split(" ")[0]
        owner = lead.get("owner") or "our team"
        hours = lead.get("sla_hours", 24)
        if lead.get("intent") == "support_issue":
            body = (
                f"Thanks for flagging this — I've logged it as a {lead.get('urgency', 'normal')} "
                f"priority issue and routed it to {owner}, who will pick it up within {hours} hours. "
                "If anything changes on your side in the meantime, reply here and it lands on the same ticket."
            )
        else:
            topic = (lead.get("category") or "general").replace("_", " ").replace("ai ", "AI ")
            body = (
                f"Thanks for reaching out about {topic}. I've passed the details to {owner}, "
                f"who will come back to you within {hours} hours with a concrete next step and "
                "a couple of questions so we can scope this properly."
            )
        return f"Hi {name},\n\n{body}\n\nKanishq — FlowPilot Automation"

    # -- shared -------------------------------------------------------------
    @staticmethod
    def _normalise(data: dict) -> dict:
        """Clamp/whitelist so downstream scoring can trust every field."""
        out = dict(data)
        out["intent"] = out.get("intent") if out.get("intent") in INTENTS else "other"
        out["category"] = out.get("category") if out.get("category") in CATEGORIES else "general"
        out["urgency"] = out.get("urgency") if out.get("urgency") in URGENCIES else "normal"
        out["sentiment"] = out.get("sentiment") if out.get("sentiment") in SENTIMENTS else "neutral"
        try:
            out["spam_score"] = min(1.0, max(0.0, float(out.get("spam_score") or 0)))
        except (TypeError, ValueError):
            out["spam_score"] = 0.0
        for key in ("person_name", "email", "phone", "company", "budget", "timeline"):
            value = out.get(key)
            out[key] = value.strip() if isinstance(value, str) and value.strip() else None
        out["summary"] = (out.get("summary") or "").strip() or "No summary available."
        points = out.get("key_points") or []
        out["key_points"] = [str(p) for p in points][:5] if isinstance(points, list) else []
        return out
