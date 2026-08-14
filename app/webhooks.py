"""Inbound webhook receiver (deliverable #2).

`POST /webhook/inbound` accepts a message from any channel, verifies an
HMAC-SHA256 signature, and hands the payload to the workflow.

Signature scheme (identical to what we emit outbound):

    X-FlowPilot-Timestamp: 1755100000
    X-FlowPilot-Signature: sha256=<hex hmac of "<timestamp>." + raw_body>

The timestamp is inside the MAC and is rejected outside a ±300s window, so a
captured request cannot be replayed later. Comparison is constant-time.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

from flask import Blueprint, current_app, jsonify, request

from .config import Config
from .workflow import LEAD_TRIAGE

log = logging.getLogger("flowpilot.webhook")

bp = Blueprint("webhooks", __name__)


def verify_signature(raw_body: bytes, headers) -> tuple[bool, str]:
    """Returns (verified, reason). Verification is skipped when no secret is set."""
    if not Config.WEBHOOK_SECRET:
        return False, "signing_disabled"

    signature = headers.get("X-FlowPilot-Signature", "")
    timestamp = headers.get("X-FlowPilot-Timestamp", "")
    if not signature or not timestamp:
        return False, "missing_signature_headers"

    try:
        skew = abs(time.time() - int(timestamp))
    except ValueError:
        return False, "bad_timestamp"
    if skew > Config.WEBHOOK_MAX_SKEW_SECONDS:
        return False, "timestamp_out_of_window"

    expected = "sha256=" + hmac.new(
        Config.WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False, "signature_mismatch"
    return True, "verified"


@bp.post("/webhook/inbound")
def inbound():
    raw = request.get_data() or b""
    verified, reason = verify_signature(raw, request.headers)

    # A configured secret is enforced; with no secret we accept but flag the run.
    if Config.WEBHOOK_SECRET and not verified:
        log.warning("rejected inbound webhook: %s", reason)
        return jsonify({"ok": False, "error": "invalid_signature", "reason": reason}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

    source = f"webhook:{payload.get('channel', 'api')}"
    run = LEAD_TRIAGE.run(payload, source=source, verified=verified)
    lead = run.get("result") or {}

    status_code = 202 if run["status"] != "failed" else 500
    return (
        jsonify(
            {
                "ok": run["status"] != "failed",
                "run_id": run["id"],
                "status": run["status"],
                "signature": reason,
                "duration_ms": run["duration_ms"],
                "lead": {
                    "id": lead.get("id"),
                    "score": lead.get("score"),
                    "tier": lead.get("tier"),
                    "intent": lead.get("intent"),
                    "owner": lead.get("owner"),
                    "sla_due_at": lead.get("sla_due_at"),
                    "summary": lead.get("summary"),
                },
            }
        ),
        status_code,
    )


@bp.get("/webhook/health")
def webhook_health():
    return jsonify(
        {
            "ok": True,
            "signing": "enforced" if Config.WEBHOOK_SECRET else "disabled",
            "max_skew_seconds": Config.WEBHOOK_MAX_SKEW_SECONDS,
            "endpoint": "/webhook/inbound",
            "version": current_app.config.get("VERSION", "1.0.0"),
        }
    )
