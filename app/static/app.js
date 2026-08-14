/* FlowPilot dashboard controller: fetch → render → interact. Vanilla JS only. */
(function () {
  "use strict";

  const state = { days: 14, tier: "", channel: "", q: "", stats: null, leads: [], tables: {} };
  const $ = sel => document.querySelector(sel);
  const fmt = n => (n === null || n === undefined ? "—" : Number(n).toLocaleString());
  const esc = s => String(s === null || s === undefined ? "" : s)
    .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ACRONYMS = { api: "API", crm: "CRM", sla: "SLA", ai: "AI", whatsapp: "WhatsApp" };
  const title = s => String(s || "").replace(/_/g, " ")
    .replace(/\b[\w']+/g, w => ACRONYMS[w.toLowerCase()] || w.charAt(0).toUpperCase() + w.slice(1));

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  function span(secs) {
    if (secs < 60) return "<1m";
    if (secs < 3600) return Math.floor(secs / 60) + "m";
    if (secs < 86400) return Math.floor(secs / 3600) + "h";
    return Math.floor(secs / 86400) + "d";
  }
  function ago(iso) {
    if (!iso) return "—";
    const secs = (Date.now() - new Date(iso).getTime()) / 1000;
    return secs < 60 ? "just now" : span(secs) + " ago";
  }
  function until(iso) {
    if (!iso) return "—";
    const secs = (new Date(iso).getTime() - Date.now()) / 1000;
    return secs <= 0 ? "overdue" : span(secs) + " left";
  }

  /* ---------------------------------------------------------------- */
  /* fetching                                                          */
  /* ---------------------------------------------------------------- */
  async function getJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function refresh(showSpinner) {
    const main = $(".wrap");
    if (showSpinner) main.classList.add("refreshing");   /* hold previous render, no skeleton flash */
    try {
      const params = new URLSearchParams({ days: state.days });
      const leadParams = new URLSearchParams({ limit: 60 });
      if (state.tier) leadParams.set("tier", state.tier);
      if (state.channel) leadParams.set("channel", state.channel);
      if (state.q) leadParams.set("q", state.q);

      const [stats, leads] = await Promise.all([
        getJSON("/api/stats?" + params),
        getJSON("/api/leads?" + leadParams),
      ]);
      state.stats = stats;
      state.leads = leads.leads;
      renderAll();
    } catch (err) {
      toast("Refresh failed: " + err.message);
    } finally {
      main.classList.remove("refreshing");
    }
  }

  /* ---------------------------------------------------------------- */
  /* render                                                            */
  /* ---------------------------------------------------------------- */
  function renderAll() {
    renderPills();
    renderTiles();
    renderCharts();
    renderIntegrations();
    renderLeads();
  }

  function renderPills() {
    const c = state.stats.config;
    const map = [
      ["AI", c.ai === "claude" ? c.ai_model : "rule engine", c.ai === "claude"],
      ["Webhook", c.webhook_signing, c.webhook_signing === "enforced"],
      ["CRM", c.crm, c.crm === "live"],
      ["Notify", c.notify, c.notify === "live"],
      ["Enrichment", c.enrichment, c.enrichment === "live"],
    ];
    $("#modePills").innerHTML = map
      .map(([k, v, live]) => `<span class="pill ${live ? "live" : "sim"}">${k}: <b>${esc(v)}</b></span>`)
      .join("");
  }

  function tile(label, value, foot, cls) {
    return `<div class="card tile"><div class="label">${label}</div>
      <div class="value">${value}</div>
      <div class="foot ${cls || ""}">${foot}</div></div>`;
  }

  function renderTiles() {
    const k = state.stats.kpis;
    const secs = (k.avg_duration_ms / 1000).toFixed(2);
    $("#tiles").innerHTML = [
      tile("Leads triaged", fmt(k.leads), `${fmt(k.runs)} workflow runs · last ${state.days} days`),
      tile("Hot leads", fmt(k.hot_leads), `avg score ${k.avg_score} / 100`, "up"),
      tile("Automation rate", k.automation_rate + "%",
        k.failed_runs ? `${k.failed_runs} failed run(s)` : "no failed runs",
        k.failed_runs ? "warn" : "up"),
      tile("Avg handling time", secs + "s", "webhook → CRM, end to end"),
      tile("Spam filtered", fmt(k.spam_filtered), "auto-archived, never routed"),
      tile("AI spend", "$" + (k.cost_usd || 0).toFixed(4), `${fmt(k.tokens)} tokens`),
    ].join("");
  }

  function renderCharts() {
    const s = state.stats;
    const c = Charts.css;

    state.tables.throughput = {
      cols: ["Day", "Leads", "Hot leads", "Runs", "Fully automated"],
      rows: s.series.map(d => [d.day, d.leads, d.hot, d.runs, d.clean]),
      render: () => Charts.lineChart($("#throughput"), {
        labels: s.series.map(d => d.day),
        series: [
          { label: "Leads triaged", values: s.series.map(d => d.leads), color: c("--series-1") },
          { label: "Hot leads", values: s.series.map(d => d.hot), color: c("--series-2") },
        ],
      }),
    };

    const tierColors = { hot: c("--ord-1"), warm: c("--ord-2"), cold: c("--ord-3"), archived: c("--ord-4") };
    state.tables.tiers = {
      cols: ["Tier", "Leads"],
      rows: s.by_tier.map(t => [title(t.key), t.value]),
      render: () => Charts.stackedBar($("#tiers"), {
        segments: s.by_tier.map(t => ({ label: title(t.key), value: t.value, color: tierColors[t.key] })),
      }),
    };

    const k = s.kpis;
    const qualified = s.by_tier.filter(t => t.key === "hot" || t.key === "warm")
      .reduce((a, t) => a + t.value, 0);
    $("#tiersFacts").innerHTML = [
      ["Qualified (hot + warm)", qualified + (k.leads ? ` (${Math.round(qualified / k.leads * 100)}%)` : ""), ""],
      ["Average lead score", k.avg_score + " / 100", ""],
      ["Past SLA, awaiting owner", k.sla_at_risk, k.sla_at_risk ? "warn" : ""],
      ["Auto-archived as spam", k.spam_filtered, ""],
    ].map(([label, value, cls]) => `<div>${label}<b class="${cls}">${esc(value)}</b></div>`).join("");

    state.tables.channels = {
      cols: ["Channel", "Leads"],
      rows: s.by_channel.map(d => [title(d.key), d.value]),
      render: () => Charts.barChart($("#channels"), {
        data: s.by_channel.map(d => ({ label: title(d.key), value: d.value })),
      }),
    };

    state.tables.intents = {
      cols: ["Intent", "Leads"],
      rows: s.by_intent.map(d => [title(d.key), d.value]),
      render: () => Charts.barChart($("#intents"), {
        data: s.by_intent.map(d => ({ label: title(d.key), value: d.value })),
      }),
    };

    Object.keys(state.tables).forEach(id => {
      const btn = document.querySelector(`[data-table-toggle="${id}"]`);
      if (btn && btn.dataset.on === "1") renderTable(id);
      else state.tables[id].render();
    });
  }

  function renderTable(id) {
    const t = state.tables[id];
    $("#" + id).innerHTML =
      `<div class="table-scroll mini-table"><table><thead><tr>` +
      t.cols.map((col, i) => `<th class="${i ? "num" : ""}">${esc(col)}</th>`).join("") +
      `</tr></thead><tbody>` +
      t.rows.map(r => `<tr>` + r.map((v, i) => `<td class="${i ? "num" : ""}">${esc(v)}</td>`).join("") + `</tr>`).join("") +
      `</tbody></table></div>`;
  }

  function renderIntegrations() {
    const rows = state.stats.integrations;
    if (!rows.length) {
      $("#integrations").innerHTML = '<div class="empty">No outbound calls yet.</div>';
      return;
    }
    $("#integrations").innerHTML =
      `<table><thead><tr><th>Target</th><th class="num">Calls</th><th class="num">OK</th>
       <th class="num">Latency</th><th>Mode</th></tr></thead><tbody>` +
      rows.map(r => `<tr>
        <td>${esc(title(r.target))}</td>
        <td class="num">${fmt(r.calls)}</td>
        <td class="num">${Math.round((r.ok / r.calls) * 100)}%</td>
        <td class="num">${Math.round(r.avg_latency_ms)} ms</td>
        <td><span class="badge ${r.simulated === r.calls ? "skipped" : "ok"}">
            <i class="dot"></i>${r.simulated === r.calls ? "simulated" : "live"}</span></td>
      </tr>`).join("") + `</tbody></table>`;
  }

  function renderLeads() {
    $("#leadCount").textContent = `${state.leads.length} shown`;
    if (!state.leads.length) {
      $("#leadsTable").innerHTML =
        '<div class="empty">No leads match these filters. Use “Simulate inbound lead” to fire one through the workflow.</div>';
      return;
    }
    $("#leadsTable").innerHTML =
      `<table><thead><tr>
        <th>Received</th><th>Contact</th><th>Channel</th><th>Intent</th>
        <th class="num">Score</th><th>Tier</th><th>Owner</th><th>SLA</th><th>Summary</th>
      </tr></thead><tbody>` +
      state.leads.map(l => {
        const overdue = l.status === "assigned" && l.sla_due_at && new Date(l.sla_due_at) < new Date();
        return `<tr class="clickable" data-run="${esc(l.run_id)}">
          <td class="muted">${ago(l.created_at)}</td>
          <td><b>${esc(l.name || "Unknown")}</b><br><span class="muted">${esc(l.company || l.email || "—")}</span></td>
          <td>${esc(title(l.channel))}</td>
          <td>${esc(title(l.intent))}</td>
          <td class="num score">${l.score}</td>
          <td><span class="badge ${esc(l.tier)}"><i class="dot"></i>${esc(l.tier)}</span></td>
          <td>${esc(l.owner || "—")}</td>
          <td class="${overdue ? "" : "muted"}" style="${overdue ? "color:var(--critical);font-weight:600" : ""}">
            ${l.tier === "archived" ? "—" : until(l.sla_due_at)}</td>
          <td class="truncate muted" title="${esc(l.summary)}">${esc(l.summary)}</td>
        </tr>`;
      }).join("") + `</tbody></table>`;

    $("#leadsTable").querySelectorAll("tr[data-run]").forEach(tr =>
      tr.addEventListener("click", () => openRun(tr.dataset.run)));
  }

  /* ---------------------------------------------------------------- */
  /* run drawer                                                        */
  /* ---------------------------------------------------------------- */
  async function openRun(runId) {
    if (!runId || runId === "null") return;
    $("#drawerBg").classList.add("open");
    $("#drawer").classList.add("open");
    $("#drawerBody").innerHTML = '<div class="empty">Loading trace…</div>';
    try {
      const run = await getJSON("/api/runs/" + runId);
      $("#drawerBody").innerHTML = renderRun(run);
      const btn = $("#replayBtn");
      if (btn) btn.addEventListener("click", async () => {
        btn.disabled = true; btn.textContent = "Replaying…";
        try {
          const res = await getJSON(`/api/runs/${runId}/replay`, { method: "POST" });
          toast("Replayed → " + res.run_id + " (" + res.status + ")");
          closeDrawer(); refresh(true);
        } catch (e) { toast("Replay failed: " + e.message); }
      });
    } catch (err) {
      $("#drawerBody").innerHTML = `<div class="empty">Could not load run: ${esc(err.message)}</div>`;
    }
  }

  function renderRun(run) {
    const lead = run.result || {};
    const steps = run.steps.map(s => `
      <li class="${esc(s.status)}">
        <span class="rail"><i></i></span>
        <span class="body">
          <span class="name">${esc(s.title || s.name)}</span>
          <span class="badge ${esc(s.status)}" style="margin-left:6px"><i class="dot"></i>${esc(s.status)}</span>
          <div class="meta">${s.duration_ms} ms · attempt${s.attempts > 1 ? "s" : ""} ${s.attempts}
            ${s.error ? " · " + esc(s.error) : ""}</div>
          ${s.output ? `<pre class="json">${esc(JSON.stringify(s.output, null, 1))}</pre>` : ""}
        </span>
      </li>`).join("");

    const deliveries = (run.deliveries || []).map(d =>
      `<tr><td>${esc(title(d.target))}</td>
        <td><span class="badge ${d.ok ? "ok" : "failed"}"><i class="dot"></i>${d.ok ? "ok" : "failed"}</span></td>
        <td class="num">${d.latency_ms ?? 0} ms</td>
        <td class="muted">${d.simulated ? "simulated" : "live " + (d.status_code ?? "")}</td></tr>`).join("");

    return `
      <h3>${esc(lead.name || "Inbound run")} <span class="muted" style="font-weight:400">· ${esc(run.id)}</span></h3>
      <div class="muted" style="font-size:12px">${esc(run.workflow)} · source ${esc(run.source)} ·
        ${esc(run.started_at)} · ${run.duration_ms} ms ·
        <span class="badge ${esc(run.status)}"><i class="dot"></i>${esc(run.status)}</span>
        ${run.verified ? '<span class="badge ok"><i class="dot"></i>signature verified</span>'
                       : '<span class="badge skipped"><i class="dot"></i>unverified</span>'}
      </div>

      <div class="section-title">Outcome</div>
      <div class="pills">
        <span class="pill">Score <b>${lead.score ?? "—"}</b></span>
        <span class="pill">Tier <b>${esc(lead.tier || "—")}</b></span>
        <span class="pill">Intent <b>${esc(title(lead.intent))}</b></span>
        <span class="pill">Urgency <b>${esc(lead.urgency || "—")}</b></span>
        <span class="pill">Owner <b>${esc(lead.owner || "—")}</b></span>
        <span class="pill">AI <b>${esc(run.ai_mode)}</b></span>
        <span class="pill">Cost <b>$${(run.cost_usd || 0).toFixed(5)}</b></span>
      </div>

      <div class="section-title">Original message</div>
      <div class="reply">${esc(lead.message || (run.payload && run.payload.message) || "—")}</div>

      ${lead.reply_draft ? `<div class="section-title">AI-drafted reply</div>
        <div class="reply">${esc(lead.reply_draft)}</div>` : ""}

      <div class="section-title">Step trace</div>
      <ul class="timeline">${steps}</ul>

      ${deliveries ? `<div class="section-title">Outbound calls</div>
        <table class="mini-table"><thead><tr><th>Target</th><th>Result</th>
        <th class="num">Latency</th><th>Mode</th></tr></thead><tbody>${deliveries}</tbody></table>` : ""}

      <div style="margin-top:18px"><button class="btn primary" id="replayBtn">Replay this payload</button></div>
    `;
  }

  function closeDrawer() {
    $("#drawerBg").classList.remove("open");
    $("#drawer").classList.remove("open");
  }

  /* ---------------------------------------------------------------- */
  /* wiring                                                            */
  /* ---------------------------------------------------------------- */
  function init() {
    const saved = localStorage.getItem("fp-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);

    $("#themeToggle").addEventListener("click", () => {
      const isDark = document.documentElement.getAttribute("data-theme") === "dark" ||
        (!document.documentElement.getAttribute("data-theme") &&
          matchMedia("(prefers-color-scheme: dark)").matches);
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("fp-theme", next);
      if (state.stats) renderCharts();
    });

    document.querySelectorAll(".seg button").forEach(btn =>
      btn.addEventListener("click", () => {
        document.querySelectorAll(".seg button").forEach(b => b.setAttribute("aria-pressed", "false"));
        btn.setAttribute("aria-pressed", "true");
        state.days = Number(btn.dataset.days);
        refresh(true);
      }));

    $("#fTier").addEventListener("change", e => { state.tier = e.target.value; refresh(true); });
    $("#fChannel").addEventListener("change", e => { state.channel = e.target.value; refresh(true); });
    let typing;
    $("#fQuery").addEventListener("input", e => {
      clearTimeout(typing);
      typing = setTimeout(() => { state.q = e.target.value.trim(); refresh(false); }, 300);
    });

    $("#btnSimulate").addEventListener("click", async e => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.textContent = "Running workflow…";
      try {
        const res = await getJSON("/api/demo/simulate", { method: "POST" });
        toast(`Run ${res.run_id} → ${res.status}`);
        await refresh(false);
        openRun(res.run_id);
      } catch (err) { toast("Simulation failed: " + err.message); }
      finally { btn.disabled = false; btn.textContent = "Simulate inbound lead"; }
    });

    document.querySelectorAll("[data-table-toggle]").forEach(btn =>
      btn.addEventListener("click", () => {
        const id = btn.dataset.tableToggle;
        const on = btn.dataset.on === "1";
        btn.dataset.on = on ? "0" : "1";
        btn.textContent = on ? "Table" : "Chart";
        on ? state.tables[id].render() : renderTable(id);
      }));

    $("#drawerBg").addEventListener("click", closeDrawer);
    $("#drawerClose").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

    let resizeTimer;
    addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (state.stats) renderCharts(); }, 180);
    });

    refresh(true);
    setInterval(() => refresh(false), 15000);   /* live control room */
  }

  document.addEventListener("DOMContentLoaded", init);
})();
