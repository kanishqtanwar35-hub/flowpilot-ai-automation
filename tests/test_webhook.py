import hashlib
import hmac
import json
import time

from app.config import Config

PAYLOAD = {
    "channel": "web_form",
    "name": "Sana Qureshi",
    "email": "sana@vertexfoods.com",
    "message": "We want to automate quote requests from WhatsApp into our CRM. Budget $15,000, 4 weeks.",
}


def signed(body: bytes, timestamp: str | None = None, secret: str | None = None) -> dict:
    ts = timestamp or str(int(time.time()))
    key = (secret or Config.WEBHOOK_SECRET).encode()
    sig = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "X-FlowPilot-Timestamp": ts,
        "X-FlowPilot-Signature": "sha256=" + sig,
    }


def test_valid_signature_is_accepted_and_returns_the_triage_result(client):
    body = json.dumps(PAYLOAD).encode()
    res = client.post("/webhook/inbound", data=body, headers=signed(body))

    assert res.status_code == 202
    data = res.get_json()
    assert data["ok"] is True
    assert data["signature"] == "verified"
    assert data["lead"]["tier"] in {"hot", "warm"}
    assert data["lead"]["owner"]


def test_tampered_body_is_rejected(client):
    body = json.dumps(PAYLOAD).encode()
    headers = signed(body)
    tampered = json.dumps({**PAYLOAD, "message": PAYLOAD["message"] + " tampered"}).encode()

    res = client.post("/webhook/inbound", data=tampered, headers=headers)
    assert res.status_code == 401
    assert res.get_json()["reason"] == "signature_mismatch"


def test_replayed_old_timestamp_is_rejected(client):
    body = json.dumps(PAYLOAD).encode()
    stale = str(int(time.time()) - Config.WEBHOOK_MAX_SKEW_SECONDS - 60)

    res = client.post("/webhook/inbound", data=body, headers=signed(body, timestamp=stale))
    assert res.status_code == 401
    assert res.get_json()["reason"] == "timestamp_out_of_window"


def test_wrong_secret_is_rejected(client):
    body = json.dumps(PAYLOAD).encode()
    res = client.post("/webhook/inbound", data=body, headers=signed(body, secret="not-the-secret"))
    assert res.status_code == 401


def test_missing_signature_headers_are_rejected(client):
    body = json.dumps(PAYLOAD).encode()
    res = client.post("/webhook/inbound", data=body, headers={"content-type": "application/json"})
    assert res.status_code == 401
    assert res.get_json()["reason"] == "missing_signature_headers"


def test_non_object_body_is_a_400(client):
    body = b"[1, 2, 3]"
    res = client.post("/webhook/inbound", data=body, headers=signed(body))
    assert res.status_code == 400
