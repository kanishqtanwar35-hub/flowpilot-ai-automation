"""Central configuration. Every knob is env-driven with a safe default."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional dependency — the app must boot without it
    from dotenv import load_dotenv

    # utf-8-sig, not utf-8: PowerShell's `Out-File -Encoding utf8` writes a BOM, and a
    # BOM makes python-dotenv read the first key as "﻿ANTHROPIC_API_KEY" — the
    # variable silently never loads and the app quietly runs in fallback mode.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")
except Exception:  # pragma: no cover - dotenv is convenience only
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class Config:
    # server
    PORT = _int("PORT", 5000)
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    DATABASE_PATH = str(BASE_DIR / os.environ.get("DATABASE_PATH", "flowpilot.db"))

    # inbound webhook security
    WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
    WEBHOOK_MAX_SKEW_SECONDS = _int("WEBHOOK_MAX_SKEW_SECONDS", 300)

    # Claude
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5")
    AI_EFFORT = os.environ.get("AI_EFFORT", "low")
    AI_MAX_TOKENS = _int("AI_MAX_TOKENS", 4000)
    # Claude Opus 5 list price, USD per million tokens (input / output).
    AI_PRICE_IN = 5.00
    AI_PRICE_OUT = 25.00

    # integrations
    CRM_BASE_URL = os.environ.get("CRM_BASE_URL", "").rstrip("/")
    CRM_API_KEY = os.environ.get("CRM_API_KEY", "")
    NOTIFY_WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")
    OUTBOUND_WEBHOOK_URL = os.environ.get("OUTBOUND_WEBHOOK_URL", "")
    OUTBOUND_SECRET = os.environ.get("OUTBOUND_SECRET", "")
    ENRICHMENT_URL = os.environ.get("ENRICHMENT_URL", "")

    HTTP_TIMEOUT = _int("HTTP_TIMEOUT_SECONDS", 8)
    HTTP_MAX_RETRIES = _int("HTTP_MAX_RETRIES", 2)

    @classmethod
    def ai_enabled(cls) -> bool:
        return bool(cls.ANTHROPIC_API_KEY)

    @classmethod
    def warnings(cls) -> list[str]:
        """Config problems worth surfacing at startup. Never quotes a secret value."""
        out = []
        if cls.ANTHROPIC_API_KEY and not cls.ANTHROPIC_API_KEY.startswith("sk-ant-"):
            out.append(
                "ANTHROPIC_API_KEY is set but does not look like an Anthropic key "
                "(expected an 'sk-ant-' prefix) — check for a stray quote, space or BOM in .env"
            )
        if not cls.WEBHOOK_SECRET:
            out.append(
                "WEBHOOK_SECRET is not set: the inbound webhook accepts unsigned requests "
                "and every run is flagged 'unverified'"
            )
        return out

    @classmethod
    def summary(cls) -> dict:
        """Shown on the dashboard so a reviewer can see what is live vs simulated."""
        return {
            "ai": "claude" if cls.ai_enabled() else "heuristic",
            "ai_model": cls.AI_MODEL if cls.ai_enabled() else "rule-engine",
            "crm": "live" if cls.CRM_BASE_URL else "simulated",
            "notify": "live" if cls.NOTIFY_WEBHOOK_URL else "simulated",
            "outbound": "live" if cls.OUTBOUND_WEBHOOK_URL else "simulated",
            "enrichment": "live" if cls.ENRICHMENT_URL else "heuristic",
            "webhook_signing": "enforced" if cls.WEBHOOK_SECRET else "disabled",
        }
