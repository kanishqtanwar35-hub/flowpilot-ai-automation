/* FlowPilot — hand-rolled SVG charts.
   No chart library, no CDN: every mark below is an SVG element we build here.
   Design rules applied: thin marks, hairline recessive grid, 4px rounded data
   ends anchored to the baseline, 2px surface gaps between fills, 2px surface
   ring on overlapping markers, selective direct labels, legend for >=2 series,
   and a table-view twin for every chart (rendered by app.js). */
(function (global) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const tip = () => document.getElementById("tooltip");

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /* round up to a multiple of 4 so all four gridline labels stay whole numbers */
  function niceMax(value) {
    if (value <= 4) return 4;
    const step = Math.ceil(value / 4);
    const pow = Math.pow(10, Math.floor(Math.log10(step)));
    const unit = step <= 10 ? step : Math.ceil(step / (pow / 2)) * (pow / 2);
    return unit * 4;
  }

  /* Label ink for text sitting on a filled mark: whichever of white/near-black
     has the higher WCAG contrast against that fill (never below ~4.3:1). */
  function inkOn(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
    if (!m) return "#ffffff";
    const n = parseInt(m[1], 16);
    const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    const L = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
    const onWhite = 1.05 / (L + 0.05);          /* white text  */
    const onInk = (L + 0.05) / (0.0055 + 0.05); /* #0b0b0b text */
    return onWhite >= onInk ? "#ffffff" : "#0b0b0b";
  }

  /* rounded rect path with per-corner radii */
  function roundPath(x, y, w, h, r) {
    const tl = r.tl || 0, tr = r.tr || 0, br = r.br || 0, bl = r.bl || 0;
    const cap = Math.min(w, h) / 2;
    const a = Math.min(tl, cap), b = Math.min(tr, cap), c = Math.min(br, cap), d = Math.min(bl, cap);
    return (
      `M${x + a},${y}` +
      `H${x + w - b}${b ? `A${b},${b} 0 0 1 ${x + w},${y + b}` : ""}` +
      `V${y + h - c}${c ? `A${c},${c} 0 0 1 ${x + w - c},${y + h}` : ""}` +
      `H${x + d}${d ? `A${d},${d} 0 0 1 ${x},${y + h - d}` : ""}` +
      `V${y + a}${a ? `A${a},${a} 0 0 1 ${x + a},${y}` : ""}Z`
    );
  }

  function showTip(evt, html) {
    const t = tip();
    t.innerHTML = html;
    t.style.left = evt.clientX + "px";
    t.style.top = evt.clientY + "px";
    t.style.opacity = "1";
  }
  function hideTip() { tip().style.opacity = "0"; }

  function mount(container, height) {
    container.innerHTML = "";
    const width = Math.max(240, container.clientWidth || 480);
    const svg = el("svg", {
      class: "chart", width: "100%", height: height,
      viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none",
      role: "img"
    }, container);
    return { svg, width, height };
  }

  function legend(container, items) {
    const wrap = document.createElement("div");
    wrap.className = "legend";
    items.forEach(function (i) {
      const s = document.createElement("span");
      s.innerHTML = `<i class="swatch" style="background:${i.color}"></i>${i.label}` +
        (i.value !== undefined ? ` <b style="color:var(--text-primary);font-weight:600">${i.value}</b>` : "");
      wrap.appendChild(s);
    });
    container.appendChild(wrap);
  }

  /* ------------------------------------------------------------------ */
  /* Multi-series line chart with crosshair + tooltip                     */
  /* ------------------------------------------------------------------ */
  function lineChart(container, opts) {
    const labels = opts.labels || [];
    const series = opts.series || [];
    if (!labels.length) { container.innerHTML = '<div class="empty">No runs in this range yet.</div>'; return; }

    const H = opts.height || 230;
    const { svg, width } = mount(container, H);
    const pad = { l: 34, r: 46, t: 14, b: 26 };
    const iw = width - pad.l - pad.r, ih = H - pad.t - pad.b;

    const max = niceMax(Math.max(1, ...series.flatMap(s => s.values)));
    const x = i => pad.l + (labels.length === 1 ? iw / 2 : (i / (labels.length - 1)) * iw);
    const y = v => pad.t + ih - (v / max) * ih;

    /* recessive grid + y ticks */
    for (let i = 0; i <= 4; i++) {
      const gy = pad.t + (ih / 4) * i;
      el("line", { class: "grid-line", x1: pad.l, x2: pad.l + iw, y1: gy, y2: gy }, svg);
      el("text", { class: "tick", x: pad.l - 8, y: gy + 4, "text-anchor": "end" }, svg)
        .textContent = String(Math.round(max - (max / 4) * i));
    }
    el("line", { class: "axis-line", x1: pad.l, x2: pad.l + iw, y1: pad.t + ih, y2: pad.t + ih }, svg);

    /* x ticks: at most 6, always first and last */
    const step = Math.max(1, Math.ceil(labels.length / 6));
    labels.forEach(function (lab, i) {
      if (i % step !== 0 && i !== labels.length - 1) return;
      el("text", { class: "tick", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : (i === labels.length - 1 ? "end" : "middle") }, svg)
        .textContent = lab.slice(5); // MM-DD
    });

    const surface = css("--surface-1");
    series.forEach(function (s) {
      const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
      el("polyline", {
        points: pts, fill: "none", stroke: s.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round"
      }, svg);
      /* endpoint marker with a 2px surface ring, plus one selective direct label */
      const li = s.values.length - 1;
      el("circle", { cx: x(li), cy: y(s.values[li]), r: 4.5, fill: s.color, stroke: surface, "stroke-width": 2 }, svg);
      el("text", {
        class: "value-lbl", x: x(li) + 9, y: y(s.values[li]) + 4, "text-anchor": "start"
      }, svg).textContent = s.values[li];
    });

    /* crosshair overlay */
    const cross = el("line", { class: "axis-line", x1: 0, x2: 0, y1: pad.t, y2: pad.t + ih, opacity: 0 }, svg);
    const dots = series.map(s => el("circle", { r: 4.5, fill: s.color, stroke: surface, "stroke-width": 2, opacity: 0 }, svg));
    const hit = el("rect", { x: pad.l, y: pad.t, width: iw, height: ih, fill: "transparent" }, svg);

    function moveTo(evt) {
      const box = svg.getBoundingClientRect();
      const rel = (evt.clientX - box.left) / box.width * width;
      let idx = Math.round((rel - pad.l) / (iw || 1) * (labels.length - 1));
      idx = Math.max(0, Math.min(labels.length - 1, idx));
      cross.setAttribute("x1", x(idx)); cross.setAttribute("x2", x(idx)); cross.setAttribute("opacity", 1);
      dots.forEach(function (d, k) {
        d.setAttribute("cx", x(idx)); d.setAttribute("cy", y(series[k].values[idx])); d.setAttribute("opacity", 1);
      });
      const rows = series.map(s =>
        `<div class="t-row"><i class="swatch" style="background:${s.color}"></i>${s.label}<b>${s.values[idx]}</b></div>`
      ).join("");
      showTip(evt, `<div class="t-head">${labels[idx]}</div>${rows}`);
    }
    hit.addEventListener("mousemove", moveTo);
    hit.addEventListener("mouseleave", function () {
      hideTip(); cross.setAttribute("opacity", 0); dots.forEach(d => d.setAttribute("opacity", 0));
    });

    legend(container, series.map(s => ({ color: s.color, label: s.label })));
  }

  /* ------------------------------------------------------------------ */
  /* Horizontal bar chart — one series, one colour, value at the bar end  */
  /* ------------------------------------------------------------------ */
  function barChart(container, opts) {
    const data = (opts.data || []).filter(d => d.value > 0);
    if (!data.length) { container.innerHTML = '<div class="empty">Nothing recorded in this range.</div>'; return; }

    const rowH = 30, H = Math.max(90, data.length * rowH + 12);
    const { svg, width } = mount(container, H);
    const labelW = Math.min(120, Math.max(72, ...data.map(d => d.label.length * 6.6)));
    const valueW = 34;
    const trackX = labelW + 10, trackW = Math.max(40, width - trackX - valueW);
    const max = Math.max(...data.map(d => d.value));
    const color = opts.color || css("--series-1");

    data.forEach(function (d, i) {
      const y = i * rowH + 6;
      const h = rowH - 12;                        /* leaves >2px surface gap between bars */
      const w = Math.max(3, (d.value / max) * trackW);

      el("text", { class: "label-in", x: labelW, y: y + h / 2 + 4, "text-anchor": "end" }, svg)
        .textContent = d.label;

      /* faint track so short bars still read as "out of max" */
      el("path", { d: roundPath(trackX, y, trackW, h, { tl: 4, tr: 4, br: 4, bl: 4 }), fill: css("--grid"), opacity: .38 }, svg);
      const bar = el("path", {
        d: roundPath(trackX, y, w, h, { tl: 0, tr: 4, br: 4, bl: 0 }), fill: color
      }, svg);
      el("text", { class: "value-lbl tick", x: trackX + trackW + 6, y: y + h / 2 + 4 }, svg)
        .textContent = d.value;

      const hit = el("rect", { x: trackX, y: y - 3, width: trackW, height: h + 6, fill: "transparent" }, svg);
      const pct = ((d.value / data.reduce((a, b) => a + b.value, 0)) * 100).toFixed(0);
      hit.addEventListener("mousemove", e => showTip(e,
        `<div class="t-head">${d.label}</div><div class="t-row">${opts.unit || "leads"}<b>${d.value}</b></div>` +
        `<div class="t-row">share<b>${pct}%</b></div>`));
      hit.addEventListener("mouseleave", hideTip);
      bar.style.pointerEvents = "none";
    });
  }

  /* ------------------------------------------------------------------ */
  /* Part-to-whole: one stacked bar, ordinal ramp, 2px surface gaps       */
  /* ------------------------------------------------------------------ */
  function stackedBar(container, opts) {
    const segs = (opts.segments || []).filter(s => s.value > 0);
    const total = segs.reduce((a, s) => a + s.value, 0);
    if (!total) { container.innerHTML = '<div class="empty">No leads in this range yet.</div>'; return; }

    const H = 78;
    const { svg, width } = mount(container, H);
    const gap = 2, barH = 34, y = 16;
    const usable = width - gap * (segs.length - 1);
    let x = 0;

    segs.forEach(function (s, i) {
      const w = Math.max(4, (s.value / total) * usable);
      const first = i === 0, last = i === segs.length - 1;
      const path = el("path", {
        d: roundPath(x, y, w, barH, { tl: first ? 4 : 0, bl: first ? 4 : 0, tr: last ? 4 : 0, br: last ? 4 : 0 }),
        fill: s.color
      }, svg);
      /* only label inside the segment when it genuinely fits */
      const pct = Math.round((s.value / total) * 100);
      if (w > 46) {
        el("text", {
          x: x + w / 2, y: y + barH / 2 + 4, "text-anchor": "middle",
          fill: inkOn(s.color), "font-weight": 600
        }, svg).textContent = pct + "%";
      }
      const hit = el("rect", { x: x, y: y - 8, width: w, height: barH + 16, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", e => showTip(e,
        `<div class="t-head">${s.label}</div><div class="t-row">leads<b>${s.value}</b></div>` +
        `<div class="t-row">share<b>${pct}%</b></div>`));
      hit.addEventListener("mouseleave", hideTip);
      path.style.pointerEvents = "none";
      x += w + gap;
    });

    legend(container, segs.map(s => ({ color: s.color, label: s.label, value: s.value })));
  }

  global.Charts = { lineChart, barChart, stackedBar, css };
})(window);
