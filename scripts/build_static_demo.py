"""Freeze the live dashboard into a single self-contained HTML file.

    python scripts/build_static_demo.py

Produces:
    docs/demo.html            standalone page — open locally or host on GitHub Pages
    docs/demo-artifact.html   same page without the <html>/<head>/<body> wrapper,
                              for hosts that supply their own document skeleton

The snapshot embeds the current database (stats, leads, full run traces) and
installs a `fetch` shim, so every filter, chart, table view and run drawer keeps
working with no backend. Nothing external is referenced: CSS, JS and data are
all inlined.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.security import redact  # noqa: E402

OUT_DIR = ROOT / "docs"
STATIC = ROOT / "app" / "static"


def collect(client) -> dict:
    data = {"stats": {}, "leads": [], "runs": {}}
    for days in (7, 14, 30):
        data["stats"][str(days)] = client.get(f"/api/stats?days={days}").get_json()

    leads = client.get("/api/leads?limit=200").get_json()["leads"]
    data["leads"] = leads

    for lead in leads[:80]:
        rid = lead.get("run_id")
        if rid and rid not in data["runs"]:
            res = client.get("/api/runs/" + rid)
            if res.status_code == 200:
                data["runs"][rid] = res.get_json()
    data["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


SHIM = """
<script>
/* Static snapshot: intercept the dashboard's API calls and answer from embedded data. */
(function () {
  const D = window.__FLOWPILOT_DEMO__;
  const runIds = Object.keys(D.runs);

  function filterLeads(params) {
    const tier = params.get("tier"), channel = params.get("channel");
    const q = (params.get("q") || "").toLowerCase();
    const limit = Number(params.get("limit") || 50);
    const rows = D.leads.filter(l =>
      (!tier || l.tier === tier) &&
      (!channel || l.channel === channel) &&
      (!q || [l.name, l.email, l.company, l.summary, l.message]
        .some(v => (v || "").toLowerCase().includes(q)))
    ).slice(0, limit);
    return { count: rows.length, leads: rows };
  }

  const json = body => Promise.resolve(new Response(JSON.stringify(body), {
    status: 200, headers: { "content-type": "application/json" }
  }));

  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    /* Parse the request path from the raw string: under file:// a relative URL
       resolves to file:///C:/api/... and `new URL(...).pathname` would carry the drive. */
    const raw = typeof input === "string" ? input : input.url;
    const cut = raw.indexOf("?");
    const p = cut === -1 ? raw : raw.slice(0, cut);
    const params = new URLSearchParams(cut === -1 ? "" : raw.slice(cut + 1));

    if (p === "/api/stats") return json(D.stats[params.get("days") || "14"] || D.stats["14"]);
    if (p === "/api/leads") return json(filterLeads(params));
    if (p.startsWith("/api/runs/") && p.endsWith("/replay"))
      return json({ ok: false, error: "replay is disabled in the static snapshot" });
    if (p.startsWith("/api/runs/")) {
      const run = D.runs[p.split("/").pop()];
      return run ? json(run) : json({ ok: false, error: "not found" });
    }
    if (p === "/api/demo/simulate") {
      const id = runIds[Math.floor(Math.random() * runIds.length)];
      return json({ ok: true, run_id: id, status: D.runs[id].status, snapshot: true });
    }
    return realFetch(input, init);
  };

  /* CSV export without a server */
  document.addEventListener("DOMContentLoaded", function () {
    const cols = ["created_at", "channel", "name", "email", "company", "intent",
                  "category", "urgency", "score", "tier", "owner", "queue", "status", "summary"];
    const esc = v => '"' + String(v === null || v === undefined ? "" : v).replace(/"/g, '""') + '"';
    const csv = [cols.join(",")].concat(D.leads.map(l => cols.map(c => esc(l[c])).join(","))).join("\\n");
    const link = document.querySelector('a[href="/api/export.csv"]');
    if (link) {
      link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      link.setAttribute("download", "flowpilot-leads.csv");
    }
    const btn = document.getElementById("btnSimulate");
    if (btn) btn.textContent = "Open a recorded run";
  });
})();
</script>
"""

BANNER = """
<div class="pill" style="display:block;margin:0 0 16px;padding:9px 13px;border-radius:10px;line-height:1.5">
  <b>Static snapshot.</b> Every chart, filter, table view and workflow trace below is live —
  the data is frozen from a real run of the FlowPilot workflow ({built_at} UTC), served from this
  page with no backend. Run the Flask app to fire real webhooks through it.
</div>
"""


def build() -> None:
    app = create_app()
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    data = collect(client)

    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    charts_js = (STATIC / "charts.js").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")

    # lambda replacements: the payloads contain backslashes that re would treat as escapes
    html = re.sub(r'<link rel="stylesheet"[^>]*>', lambda _: f"<style>\n{css}\n</style>", html)
    # The snapshot is meant to be published: scrub secrets from every embedded string.
    payload = json.dumps(redact(data), separators=(",", ":")).replace("</", "<\\/")
    html = re.sub(
        r'<script src="[^"]*charts\.js[^"]*"></script>',
        lambda _: (f"<script>window.__FLOWPILOT_DEMO__ = {payload};</script>\n{SHIM}\n"
                   f"<script>\n{charts_js}\n</script>"),
        html,
    )
    html = re.sub(r'<script src="[^"]*app\.js[^"]*"></script>',
                  lambda _: f"<script>\n{app_js}\n</script>", html)
    html = html.replace(
        '<section class="tiles" id="tiles"></section>',
        BANNER.format(built_at=data["built_at"].replace("T", " ").replace("+00:00", ""))
        + '<section class="tiles" id="tiles"></section>',
    )

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "demo.html").write_text(html, encoding="utf-8")

    # body-only variant for hosts that inject their own document skeleton
    head = re.search(r"<head>(.*?)</head>", html, re.S)
    body = re.search(r"<body>(.*?)</body>", html, re.S)
    keep = "\n".join(
        m.group(0) for m in re.finditer(r"<title>.*?</title>|<style>.*?</style>", head.group(1), re.S)
    )
    (OUT_DIR / "demo-artifact.html").write_text(keep + "\n" + body.group(1), encoding="utf-8")

    size = (OUT_DIR / "demo.html").stat().st_size / 1024
    print(f"✓ docs/demo.html            {size:,.0f} KB")
    print(f"✓ docs/demo-artifact.html   ({len(data['leads'])} leads, {len(data['runs'])} run traces)")


if __name__ == "__main__":
    build()
