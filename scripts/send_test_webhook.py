"""Fire a correctly-signed request at the inbound webhook.

    python scripts/send_test_webhook.py
    python scripts/send_test_webhook.py --message "our API sync is broken" --channel whatsapp
    python scripts/send_test_webhook.py --tamper       # proves the HMAC check rejects it
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Config  # noqa: E402

DEFAULT_MESSAGE = (
    "Hi, we're a 40-person logistics firm and want to automate quote requests coming in "
    "over WhatsApp and email into our CRM. Budget around $15,000, need it live in 4 weeks. "
    "Can we talk this week?"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=f"http://127.0.0.1:{Config.PORT}/webhook/inbound")
    ap.add_argument("--channel", default="web_form")
    ap.add_argument("--name", default="Sana Qureshi")
    ap.add_argument("--email", default="sana@vertexfoods.com")
    ap.add_argument("--message", default=DEFAULT_MESSAGE)
    ap.add_argument("--secret", default=Config.WEBHOOK_SECRET)
    ap.add_argument("--tamper", action="store_true", help="corrupt the body after signing")
    args = ap.parse_args()

    payload = {"channel": args.channel, "name": args.name, "email": args.email, "message": args.message}
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))

    headers = {"content-type": "application/json"}
    if args.secret:
        sig = hmac.new(args.secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        headers["X-FlowPilot-Timestamp"] = timestamp
        headers["X-FlowPilot-Signature"] = "sha256=" + sig
    else:
        print("! WEBHOOK_SECRET is not set — sending unsigned (the server will flag the run)")

    if args.tamper:
        payload["message"] = payload["message"] + " (tampered in transit)"
        body = json.dumps(payload).encode()
        print("! body modified after signing — expect HTTP 401")

    print(f"→ POST {args.url}")
    response = httpx.post(args.url, content=body, headers=headers, timeout=60)
    print(f"← HTTP {response.status_code}")
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
