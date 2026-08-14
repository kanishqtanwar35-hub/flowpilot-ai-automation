"""Test fixtures. Environment must be set BEFORE app modules import Config."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "flowpilot_test.db"))
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-value")
os.environ["ANTHROPIC_API_KEY"] = ""          # force the deterministic heuristic engine
os.environ["CRM_BASE_URL"] = ""               # keep every integration simulated
os.environ["NOTIFY_WEBHOOK_URL"] = ""
os.environ["OUTBOUND_WEBHOOK_URL"] = ""
os.environ["ENRICHMENT_URL"] = ""

import pytest  # noqa: E402

from app import create_app, db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    db.init_db()
    db.reset_db()
    yield


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    return app.test_client()
