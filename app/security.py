"""Secret hygiene.

The API key never has to appear anywhere except the outbound Anthropic request,
but secrets leak through side channels: an SDK error message that quotes the
request, a log line, a stored step output, a webhook URL with the token in its
path. This module is the single choke point that scrubs them.

Two layers, because either one alone is insufficient:

1. **Exact-value redaction** — every configured secret's *actual value* is
   matched literally. Catches anything, however it got there.
2. **Pattern redaction** — known token shapes (``sk-ant-…``, ``Bearer …``,
   ``xoxb-…``, ``ghp_…``, ``whsec_…``). Catches keys we were never told about,
   e.g. one a user pasted into a message body.

Anything persisted, logged, returned by the API, or baked into the static demo
goes through :func:`redact` first. URLs additionally go through :func:`safe_url`,
because a Slack incoming-webhook URL *is* a credential.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

MASK = "[redacted]"   # ASCII on purpose: survives JSON escaping, CSV and log encodings intact

# Token shapes worth catching even when we were never handed the value.
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                 # Anthropic API key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # generic provider key
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}"),            # Slack token
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),              # GitHub token
    re.compile(r"\bwhsec_[A-Za-z0-9+/=_\-]{16,}"),            # webhook signing secret
    # Require a token-shaped value, so documentation like `Authorization: Bearer <key>`
    # and f-strings like f"Bearer {api_key}" are not flagged as leaks.
    re.compile(r"(?i)\b(?:authorization|x-api-key)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._\-+/=]{16,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    # Require a token-shaped path so prose like ".../services/…" is not flagged.
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{12,}"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/[A-Za-z0-9/_\-]{12,}"),
]

# Config attributes whose literal value must never surface.
_SECRET_ATTRS = (
    "ANTHROPIC_API_KEY",
    "WEBHOOK_SECRET",
    "CRM_API_KEY",
    "OUTBOUND_SECRET",
    "NOTIFY_WEBHOOK_URL",
    "OUTBOUND_WEBHOOK_URL",
)

_MIN_LITERAL_LEN = 8  # below this, literal matching would mangle ordinary text


def live_secrets() -> list[str]:
    """Current secret values, longest first so the broadest match wins."""
    from .config import Config

    values = {
        str(getattr(Config, attr, "") or "")
        for attr in _SECRET_ATTRS
    }
    return sorted((v for v in values if len(v) >= _MIN_LITERAL_LEN), key=len, reverse=True)


def redact(value):
    """Scrub secrets from a string (or recursively from a dict/list/tuple)."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    if not isinstance(value, str) or not value:
        return value

    out = value
    for secret in live_secrets():
        if secret in out:
            out = out.replace(secret, MASK)
    for pattern in PATTERNS:
        out = pattern.sub(MASK, out)
    return out


def safe_url(url: str | None) -> str | None:
    """Keep a URL identifiable without keeping the credential in it.

    ``https://hooks.slack.com/services/T01/B02/xxxxSECRETxxxx``
        → ``https://hooks.slack.com/services/…``

    Userinfo is dropped, the query string is dropped, and any path segment that
    looks like a token (long, or high-entropy-ish) is collapsed to ``…``.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.netloc:
        return redact(url)

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"

    kept, elided = [], False
    for segment in [s for s in parts.path.split("/") if s]:
        if len(segment) > 16 or re.fullmatch(r"[A-Za-z0-9_\-]{12,}", segment):
            elided = True
            break
        kept.append(segment)
    path = "/" + "/".join(kept) if kept else ""
    if elided:
        path = path + "/…"
    return f"{parts.scheme}://{host}{path}" + ("?…" if parts.query else "")


class RedactingFilter(logging.Filter):
    """Applied to the root logger: no secret reaches a log sink, ever."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                record.args = redact(record.args)
        except Exception:  # pragma: no cover - logging must never raise
            pass
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
