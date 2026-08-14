# Handling the API key (and every other secret)

The threat isn't someone reading `.env` off your laptop. It's the boring paths: a key pasted
into a file and pushed, an SDK error quoting the request into a log, a stored trace rendered
into a dashboard, a Slack webhook URL — which *is* a credential — sitting in an audit row.

FlowPilot closes each of those deliberately.

## Where the key is allowed to exist

| Place | Allowed | Why |
|---|---|---|
| `.env` on your machine | ✅ | gitignored (`.env`, `.env.*`, with `!.env.example` re-included) |
| Process environment | ✅ | read once into `Config` at import |
| Outbound request to `api.anthropic.com` | ✅ | the SDK sets the header; we never build it by hand |
| Source code, tests, fixtures | ❌ | scanner blocks the commit |
| Logs | ❌ | redacting filter on the root logger and every handler |
| Database, run traces, API responses | ❌ | redacted before write |
| `docs/demo.html` (the published snapshot) | ❌ | redaction pass over every embedded string at build time |

`Config.summary()` — the thing the dashboard and `/api/health` show — reports **modes**
(`claude` / `heuristic`, `live` / `simulated`, `enforced` / `disabled`), never values. There is no
endpoint, template or log line that renders a secret.

## The two redaction layers

[`app/security.py`](../app/security.py) is the single choke point. Both layers run on every string:

1. **Exact-value** — each configured secret's real value is matched literally. Catches a leak
   however it got there, including a key embedded in a URL or an unfamiliar error format.
2. **Pattern** — known token shapes: `sk-ant-…`, `sk-…`, `xoxb-…`, `ghp_…`, `whsec_…`,
   `Authorization:`/`Bearer` headers, Slack and Discord webhook URLs. Catches keys we were never
   told about — for example one a *user* pastes into an enquiry.

Patterns require a token-shaped value, so documentation (`Authorization: Bearer <key>`) and
f-strings (`f"Bearer {api_key}"`) are not false positives.

`safe_url()` handles credential-bearing URLs separately: a Slack webhook stored in the delivery
audit becomes `https://hooks.slack.com/services/…` — still identifiable, no longer usable.

**Verified end to end.** With a key loaded and an intentionally invalid value, the live 401 from
Anthropic produces this stored trace, and the run still completes on the fallback engine:

```
ai_extract   : heuristic-fallback
stored note  : claude_error: AuthenticationError: Error code: 401 - {'type': 'error',
               'error': {'type': 'authentication_error', 'message': 'API key is invalid.'}}
KEY IN API RESPONSE?  False
```

## Stopping a commit before it happens

```bash
python scripts/scan_secrets.py --install-hook   # do this once, right after git init
```

Every commit now runs the scanner against the staged diff and fails if it finds:

1. a secret file staged (`.env`, `credentials.json`, `id_rsa`, …) — the most common leak
2. a secret file already tracked from an earlier commit
3. a token-shaped string in any staged content
4. a `.gitignore` with no `.env` rule

Run it manually any time: `python scripts/scan_secrets.py` (whole tree) or `--staged`.
A deliberate false positive can be waived by putting `secret-scan: allow` on that line.

A `.env` that exists locally and is ignored is **not** a finding — that is the setup we want.

## If a key was ever committed

Rotating is the only fix. Removing the file in a later commit does nothing: the value is in the
history, and on a public repo it is scraped within minutes.

1. Revoke the key in the Anthropic Console and issue a new one.
2. Put the new key in `.env` — never in a file git tracks.
3. Only then worry about scrubbing history (`git filter-repo`), and treat that as cleanup, not
   as remediation.

The same applies to a Slack webhook URL, a GitHub PAT, or the CRM bearer token.

## Windows footgun worth knowing

`Out-File -Encoding utf8` in Windows PowerShell writes a **BOM**. A BOM makes python-dotenv read
the first line as `﻿ANTHROPIC_API_KEY`, so that variable silently never loads and the app
quietly runs in fallback mode — which looks exactly like "the AI isn't working".

Two guards: `load_dotenv(..., encoding="utf-8-sig")` strips it, and `Config.warnings()` logs a
startup warning if the key doesn't start with `sk-ant-` (without ever printing the key).

Safe ways to write the file:

```powershell
Copy-Item .env.example .env; notepad .env                       # simplest
"ANTHROPIC_API_KEY=sk-ant-…" | Out-File .env -Encoding ascii    # no BOM
```

## Not covered here

This is application-level hygiene, not a deployment security review. Before this faces the
internet you also want: TLS termination, per-source rate limiting on the webhook, secrets from a
manager rather than a file, and log shipping with retention limits. Those are listed in
[ARCHITECTURE.md](ARCHITECTURE.md).
