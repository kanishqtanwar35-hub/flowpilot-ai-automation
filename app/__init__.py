"""FlowPilot — AI Automation Suite.

Flask application factory: wires the dashboard, the JSON API and the inbound
webhook receiver onto one process.
"""
from __future__ import annotations

import logging

from flask import Flask, render_template

from . import db
from .config import Config
from .security import install_log_redaction, redact

VERSION = "1.0.0"


def create_app() -> Flask:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    install_log_redaction()   # before anything can log

    for warning in Config.warnings():
        logging.getLogger("flowpilot").warning(warning)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["VERSION"] = VERSION
    app.config["JSON_SORT_KEYS"] = False

    db.init_db()

    from .api import bp as api_bp
    from .webhooks import bp as webhook_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(webhook_bp)

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html", version=VERSION, config=Config.summary())

    @app.errorhandler(404)
    def not_found(_):
        return {"ok": False, "error": "not found"}, 404

    @app.errorhandler(500)
    def server_error(exc):  # pragma: no cover
        app.logger.exception("unhandled error")
        # never echo a raw exception to the client — it can quote request data
        return {"ok": False, "error": redact(str(exc))}, 500

    return app
