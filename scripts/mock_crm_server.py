"""A tiny REST "CRM" so the integration step can be demoed against a real endpoint.

    python scripts/mock_crm_server.py           # listens on 127.0.0.1:5055
    # then in .env:  CRM_BASE_URL=http://127.0.0.1:5055   CRM_API_KEY=demo-key

Endpoints
    POST /contacts        upsert a contact (requires `Authorization: Bearer <key>`)
    GET  /contacts        list everything received
    POST /hooks/notify    accepts Slack-shaped notification payloads
    POST /hooks/events    accepts our signed outbound webhook and verifies the HMAC
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

API_KEY = os.environ.get("MOCK_CRM_KEY", "demo-key")
OUTBOUND_SECRET = os.environ.get("OUTBOUND_SECRET", "")
STORE: dict[str, dict] = {}
LOG: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> tuple[bytes, dict]:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return raw, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return raw, {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/contacts"):
            self._send(200, {"count": len(STORE), "contacts": list(STORE.values())})
        elif self.path.startswith("/log"):
            self._send(200, {"events": LOG[-50:]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        raw, body = self._body()

        if self.path.startswith("/contacts"):
            if self.headers.get("authorization") != f"Bearer {API_KEY}":
                return self._send(401, {"error": "bad api key"})
            cid = body.get("id") or f"crm_{len(STORE) + 1}"
            existing = STORE.get(cid, {})
            STORE[cid] = {**existing, **body, "id": cid, "updated_at": time.time()}
            return self._send(200, {"ok": True, "contact_id": cid, "created": cid not in existing})

        if self.path.startswith("/hooks/notify"):
            LOG.append({"kind": "notify", "body": body})
            print("  [notify]", (body.get("text") or body.get("content", "")).split("\n")[0])
            return self._send(200, {"ok": True})

        if self.path.startswith("/hooks/events"):
            verified = None
            if OUTBOUND_SECRET:
                ts = self.headers.get("X-FlowPilot-Timestamp", "")
                sig = self.headers.get("X-FlowPilot-Signature", "")
                expected = "sha256=" + hmac.new(
                    OUTBOUND_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256
                ).hexdigest()
                verified = hmac.compare_digest(expected, sig)
                if not verified:
                    return self._send(401, {"ok": False, "error": "bad signature"})
            LOG.append({"kind": "event", "type": body.get("type"), "verified": verified})
            print(f"  [event] {body.get('type')} signature_verified={verified}")
            return self._send(200, {"ok": True, "signature_verified": verified})

        self._send(404, {"error": "not found"})

    def log_message(self, *_):  # keep the console readable
        return


if __name__ == "__main__":
    print("Mock CRM listening on http://127.0.0.1:5055")
    print(f"  API key: {API_KEY}   (set CRM_BASE_URL=http://127.0.0.1:5055 in .env)")
    print("  POST /contacts · POST /hooks/notify · POST /hooks/events · GET /contacts\n")
    HTTPServer(("127.0.0.1", 5055), Handler).serve_forever()
