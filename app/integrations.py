"""Third-party integration layer (deliverable #2).

Everything that leaves the process goes through :func:`http_call`, which owns
timeouts, bounded retries with exponential backoff, HMAC signing of outbound
webhooks, and an audit row in the ``deliveries`` table.

Any integration whose URL is not configured returns a ``simulated`` result
instead of raising, so the whole workflow still completes end-to-end offline —
and the dashboard shows plainly which hops were live and which were simulated.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db
from .config import Config
from .security import redact, safe_url

log = logging.getLogger("flowpilot.integrations")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sign_payload(body: bytes, secret: str, timestamp: str) -> str:
    """`sha256=<hex>` over `timestamp.body` — the same scheme we verify inbound."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


class DeliveryResult(dict):
    """dict subclass so it serialises straight into the step output."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def http_call(
    target: str,
    method: str,
    url: str,
    *,
    run_id: str | None = None,
    json_body: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    sign_secret: str = "",
) -> DeliveryResult:
    """One outbound call with retries, signing and an audit row."""
    body = json.dumps(json_body, default=str).encode() if json_body is not None else None
    hdrs = {"content-type": "application/json", "user-agent": "FlowPilot/1.0"}
    hdrs.update(headers or {})
    if sign_secret and body is not None:
        ts = str(int(time.time()))
        hdrs["X-FlowPilot-Timestamp"] = ts
        hdrs["X-FlowPilot-Signature"] = sign_payload(body, sign_secret, ts)

    attempts, last_error, started = 0, None, time.perf_counter()
    result: DeliveryResult | None = None

    while attempts <= Config.HTTP_MAX_RETRIES:
        attempts += 1
        try:
            with httpx.Client(timeout=Config.HTTP_TIMEOUT) as client:
                resp = client.request(method, url, content=body, params=params, headers=hdrs)
            payload: Any
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001 - non-JSON responses are fine
                payload = resp.text[:500]

            if resp.status_code >= 500 and attempts <= Config.HTTP_MAX_RETRIES:
                last_error = f"HTTP {resp.status_code}"
                time.sleep(0.4 * 2 ** (attempts - 1))
                continue

            result = DeliveryResult(
                ok=resp.is_success,
                simulated=False,
                status_code=resp.status_code,
                attempts=attempts,
                response=payload,
                endpoint=url,
            )
            break
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            last_error = f"{type(exc).__name__}: {exc}"
            if attempts > Config.HTTP_MAX_RETRIES:
                break
            time.sleep(0.4 * 2 ** (attempts - 1))

    if result is None:
        result = DeliveryResult(
            ok=False, simulated=False, status_code=None, attempts=attempts,
            error=last_error, endpoint=url,
        )

    latency = int((time.perf_counter() - started) * 1000)
    result["latency_ms"] = latency
    _audit(target, run_id, result)
    return result


def simulated(target: str, run_id: str | None, detail: dict) -> DeliveryResult:
    result = DeliveryResult(
        ok=True, simulated=True, status_code=None, attempts=0,
        latency_ms=0, response=detail, endpoint=None,
    )
    _audit(target, run_id, result)
    return result


def _audit(target: str, run_id: str | None, result: DeliveryResult) -> None:
    try:
        db.record_delivery(
            {
                "run_id": run_id,
                "target": target,
                # a Slack/Discord webhook URL *is* a credential — store it identifiable, not usable
                "endpoint": safe_url(result.get("endpoint")),
                "ok": 1 if result.get("ok") else 0,
                "status_code": result.get("status_code"),
                "simulated": 1 if result.get("simulated") else 0,
                "latency_ms": result.get("latency_ms"),
                "detail": redact(
                    json.dumps(
                        {
                            k: (safe_url(v) if k == "endpoint" else v)
                            for k, v in result.items()
                            if k != "response"
                        },
                        default=str,
                    )
                )[:1000],
                "created_at": _now(),
            }
        )
    except Exception:  # pragma: no cover - auditing must never break a run
        log.exception("failed to record delivery audit row")


# --------------------------------------------------------------------------- #
# Concrete integrations
# --------------------------------------------------------------------------- #
def enrich_company(email: str | None, run_id: str | None = None) -> DeliveryResult:
    """Company enrichment from the email domain.

    Live: `GET {ENRICHMENT_URL}?domain=…`. Otherwise a local heuristic that is
    still genuinely useful: free-mail detection + domain-derived company name.
    """
    from .ai import FREE_MAIL  # local import avoids a cycle

    domain = email.split("@")[-1].lower() if email and "@" in email else None
    if not domain:
        return simulated("enrichment", run_id, {"domain": None, "reason": "no email"})

    if Config.ENRICHMENT_URL:
        res = http_call("enrichment", "GET", Config.ENRICHMENT_URL,
                        run_id=run_id, params={"domain": domain})
        if res.ok:
            return res
        log.info("enrichment endpoint failed, using heuristic")

    is_business = domain not in FREE_MAIL
    tld = domain.rsplit(".", 1)[-1]
    country = {"in": "IN", "uk": "GB", "au": "AU", "ca": "CA", "de": "DE", "ae": "AE"}.get(tld)
    return simulated(
        "enrichment",
        run_id,
        {
            "domain": domain,
            "is_business_email": is_business,
            "company_guess": domain.split(".")[0].replace("-", " ").title() if is_business else None,
            "country": country,
            "source": "heuristic",
        },
    )


def crm_upsert(contact: dict, run_id: str | None = None) -> DeliveryResult:
    """Push the qualified contact into a CRM over REST."""
    if not Config.CRM_BASE_URL:
        return simulated("crm", run_id, {"action": "upsert", "contact_id": f"sim_{contact.get('id')}"})
    headers = {"Authorization": f"Bearer {Config.CRM_API_KEY}"} if Config.CRM_API_KEY else {}
    return http_call(
        "crm", "POST", f"{Config.CRM_BASE_URL}/contacts",
        run_id=run_id, json_body=contact, headers=headers,
    )


def notify(lead: dict, run_id: str | None = None) -> DeliveryResult:
    """Human-in-the-loop ping to Slack / Discord / Teams / anything HTTPS."""
    title = (
        f"{lead.get('tier', 'NEW').upper()} lead · score {lead.get('score')} · "
        f"{(lead.get('intent') or '').replace('_', ' ')}"
    )
    lines = [
        title,
        f"From: {lead.get('name') or 'Unknown'} <{lead.get('email') or 'no email'}>"
        + (f" @ {lead['company']}" if lead.get("company") else ""),
        f"Channel: {lead.get('channel')} · Urgency: {lead.get('urgency')} · SLA: {lead.get('sla_due_at')}",
        f"Owner: {lead.get('owner')} ({lead.get('queue')})",
        f"Summary: {lead.get('summary')}",
    ]
    text = "\n".join(lines)

    if not Config.NOTIFY_WEBHOOK_URL:
        return simulated("notify", run_id, {"text": text})

    url = Config.NOTIFY_WEBHOOK_URL
    # Slack and Discord disagree on the field name; support both.
    body = {"content": text} if "discord.com" in url else {"text": text}
    return http_call("notify", "POST", url, run_id=run_id, json_body=body)


def fan_out(event: dict, run_id: str | None = None) -> DeliveryResult:
    """Signed outbound webhook so downstream systems can subscribe."""
    if not Config.OUTBOUND_WEBHOOK_URL:
        return simulated("outbound_webhook", run_id, {"event": event.get("type")})
    return http_call(
        "outbound_webhook", "POST", Config.OUTBOUND_WEBHOOK_URL,
        run_id=run_id, json_body=event, sign_secret=Config.OUTBOUND_SECRET,
    )
