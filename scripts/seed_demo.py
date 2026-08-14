"""Seed the dashboard with realistic history.

    python scripts/seed_demo.py --days 14 --count 70 [--reset]

Each record is pushed through the *real* workflow (so every step, delivery and
score is genuine), then its timestamps are back-dated so the charts have a
believable 14-day shape.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import Config  # noqa: E402
from app.workflow import LEAD_TRIAGE  # noqa: E402

FIRST = ["Priya", "Arun", "Meera", "Rahul", "Daniel", "Sofia", "Tom", "Ananya", "Ibrahim",
         "Chen", "Nikhil", "Grace", "Vikram", "Laura", "Omar", "Divya"]
LAST = ["Sharma", "Iyer", "Menon", "Okafor", "Blake", "Rossi", "Wang", "Fernandes",
        "Kapoor", "Haddad", "Nair", "Bennett"]
BIZ = ["northwind-retail.com", "brightpath.io", "lumenhealth.in", "blake-logistics.co.uk",
       "vertexfoods.com", "adrianotech.de", "coastline-realty.com", "kiranfintech.in"]
FREE = ["gmail.com", "outlook.com", "yahoo.com"]

NEW_BUSINESS = [
    "We run {n} retail outlets and want to automate WhatsApp order updates plus a support "
    "chatbot on the website. Budget is around ₹{b},00,000 and we'd like to start within {w} weeks.",
    "Looking for someone to build an AI agent that reads our supplier PDFs and pushes line "
    "items into our ERP. Can we get a demo this week? Budget approx $12,000.",
    "We need to automate lead routing from our website into HubSpot with scoring. Timeline is "
    "next month. Can you share a proposal?",
    "Interested in an AI dashboard that summarises our support tickets daily. Exploring options "
    "for next quarter, no rush.",
    "Can you build a WhatsApp bot that books appointments and syncs to Google Calendar? We want "
    "this live before the festive season, so within 3 weeks ideally.",
]
SUPPORT = [
    "URGENT - the invoice sync between our CRM and Tally has been failing since yesterday. "
    "Nothing is coming through and this is blocking month-end close.",
    "The chatbot stopped responding on our staging site this morning. Getting a 502 error. "
    "Can someone look today?",
    "Our nightly report email hasn't arrived for 3 days. Still not fixed after the last ticket — "
    "this is getting frustrating.",
    "Small bug: the dashboard shows duplicate rows after the last deploy. No rush, but please log it.",
]
PRICING = [
    "Following up on pricing for the AI dashboard build. How much would a 3-month engagement "
    "cost, and what does support look like after handover?",
    "What's your rate card for automation work? We're comparing 3 vendors this week.",
    "How much for a WhatsApp automation with about 2000 conversations a month?",
]
OTHER = [
    "We're a Shopify agency and would like to explore a white label partnership for AI builds.",
    "Sharing my CV for the AI engineer internship — 2 years of Python and LangChain experience.",
    "Great work on the case study you published. Do you have anything similar in logistics?",
]
SPAM = [
    "Hello sir, we provide 5000 high quality backlinks and guaranteed page 1 ranking, very cheap "
    "price. Reply for package list.",
    "INVEST NOW in crypto arbitrage bot, 300% monthly returns guaranteed. Limited slots.",
    "We offer bulk SMS and cheap traffic packages for your website. Unsubscribe to stop.",
]
CHANNELS = ["web_form", "web_form", "web_form", "email", "email", "whatsapp", "chat_widget", "api"]


def build_payload() -> dict:
    roll = random.random()
    if roll < 0.42:
        message = random.choice(NEW_BUSINESS).format(
            n=random.choice([6, 9, 14, 22]), b=random.choice([3, 4, 6, 8]), w=random.choice([2, 3, 4])
        )
    elif roll < 0.66:
        message = random.choice(SUPPORT)
    elif roll < 0.80:
        message = random.choice(PRICING)
    elif roll < 0.92:
        message = random.choice(OTHER)
    else:
        message = random.choice(SPAM)

    spammy = message in SPAM
    name = f"{random.choice(FIRST)} {random.choice(LAST)}"
    domain = random.choice(FREE if spammy or random.random() < 0.25 else BIZ)
    handle = name.split()[0].lower() if not spammy else "seo.master.999"
    channel = random.choice(CHANNELS)

    payload = {"channel": channel, "name": "" if spammy else name,
               "email": f"{handle}@{domain}", "message": message}
    if channel == "whatsapp":
        payload["phone"] = f"+91 9{random.randint(100000000, 999999999)}"
    return payload


def backdate(run_id: str, lead_id: str | None, when: datetime) -> None:
    stamp = when.isoformat(timespec="seconds")
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET started_at = ?, finished_at = ? WHERE id = ?", (stamp, stamp, run_id))
        conn.execute("UPDATE deliveries SET created_at = ? WHERE run_id = ?", (stamp, run_id))
        if lead_id:
            due = (when + timedelta(hours=random.choice([2, 8, 48]))).isoformat(timespec="seconds")
            conn.execute("UPDATE leads SET created_at = ?, sla_due_at = ? WHERE id = ?",
                         (stamp, due, lead_id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--count", type=int, default=70)
    parser.add_argument("--reset", action="store_true", help="wipe existing data first")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    db.init_db()
    if args.reset:
        db.reset_db()
        print("· cleared existing data")

    print(f"· seeding {args.count} runs across {args.days} days  (AI mode: {Config.summary()['ai']})")
    now = datetime.now(timezone.utc)
    ok = 0
    for i in range(args.count):
        payload = build_payload()
        run = LEAD_TRIAGE.run(payload, source=f"seed:{payload['channel']}", verified=True)
        # weight recent days a little heavier, like real inbound traffic
        day_offset = min(args.days - 1, int(abs(random.gauss(0, args.days / 2.4))))
        when = now - timedelta(days=day_offset, hours=random.randint(0, 23), minutes=random.randint(0, 59))
        backdate(run["id"], run.get("lead_id"), when)
        ok += run["status"] != "failed"
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{args.count}")

    print(f"\n✓ {ok}/{args.count} runs completed. Start the app and open the dashboard:")
    print(f"    python run.py   ->   http://127.0.0.1:{Config.PORT}\n")


if __name__ == "__main__":
    main()
