"""SQLite persistence. No ORM — the schema is small and the queries are explicit."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from .config import Config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    duration_ms   INTEGER,
    error         TEXT,
    lead_id       TEXT,
    ai_mode       TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    payload_json  TEXT,
    result_json   TEXT
);

CREATE TABLE IF NOT EXISTS run_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    title       TEXT,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER,
    error       TEXT,
    output_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS leads (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    channel     TEXT,
    name        TEXT,
    email       TEXT,
    phone       TEXT,
    company     TEXT,
    country     TEXT,
    message     TEXT,
    intent      TEXT,
    category    TEXT,
    sentiment   TEXT,
    urgency     TEXT,
    budget      TEXT,
    timeline    TEXT,
    spam_score  REAL DEFAULT 0,
    score       INTEGER DEFAULT 0,
    tier        TEXT,
    owner       TEXT,
    queue       TEXT,
    sla_due_at  TEXT,
    status      TEXT,
    summary     TEXT,
    reply_draft TEXT,
    run_id      TEXT
);

CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    target      TEXT NOT NULL,
    endpoint    TEXT,
    ok          INTEGER NOT NULL,
    status_code INTEGER,
    simulated   INTEGER NOT NULL DEFAULT 0,
    latency_ms  INTEGER,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started  ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_steps_run     ON run_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_deliv_run     ON deliveries(run_id);
"""


def get_connection() -> sqlite3.Connection:
    """One connection per thread (Flask's dev server is threaded)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(Config.DATABASE_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    with transaction() as conn:
        for table in ("run_steps", "deliveries", "runs", "leads"):
            conn.execute(f"DELETE FROM {table}")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


def insert(table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with transaction() as conn:
        conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def query(sql: str, params: Iterable = ()) -> list[dict]:
    rows = get_connection().execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable = ()) -> dict | None:
    row = get_connection().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def scalar(sql: str, params: Iterable = (), default: Any = 0) -> Any:
    row = get_connection().execute(sql, tuple(params)).fetchone()
    if not row or row[0] is None:
        return default
    return row[0]


# --------------------------------------------------------------------------- #
# domain writes
# --------------------------------------------------------------------------- #
def save_run(run: dict, steps: list[dict]) -> None:
    payload = dict(run)
    payload["payload_json"] = _dumps(payload.pop("payload", None))
    payload["result_json"] = _dumps(payload.pop("result", None))
    insert("runs", payload)
    with transaction() as conn:
        conn.execute("DELETE FROM run_steps WHERE run_id = ?", (run["id"],))
        for idx, step in enumerate(steps):
            conn.execute(
                """INSERT INTO run_steps
                   (run_id, idx, name, title, status, attempts, duration_ms, error, output_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run["id"],
                    idx,
                    step["name"],
                    step.get("title"),
                    step["status"],
                    step.get("attempts", 1),
                    step.get("duration_ms"),
                    step.get("error"),
                    _dumps(step.get("output")),
                ),
            )


def save_lead(lead: dict) -> None:
    insert("leads", lead)


def record_delivery(row: dict) -> None:
    insert("deliveries", row)
