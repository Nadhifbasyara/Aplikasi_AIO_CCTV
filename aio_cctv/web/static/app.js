// Dashboard web lokal AIO-CCTV (Fase 8 §8). Tanpa pustaka eksternal agar jalan tanpa internet.
"use strict";

const PALETTE = ["#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00ACC1"];
const view = document.getElementById("view");
let timers = [];            // interval milik halaman aktif, dibersihkan saat pindah halaman

// ------------------------------------------------------------------ util
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}
const fmt = (v, d = 1) => v === null || v === undefined ? "–"
  : Number.isInteger(v) ? String(v) : Number(v).toFixed(d);
const fmtTime = ts => new Date(ts * 1000).toLocaleString("id-ID",
  { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const today = () => new Date().toISOString().slice(0, 10);

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401) { showLogin(); throw new Error("perlu login"); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

function clearTimers() {
  timers.forEach(clearInterval);
  timers = [];
  // hentikan stream MJPEG yang mungkin masih terbuka di halaman sebelumnya
  view.querySelectorAll("img[data-live]").forEach(img => { img.src = ""; });
}

// ------------------------------------------------------------------ login
function showLogin() { document.getElementById("login").hidden = false; }
document.getElementById("login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = document.getElementById("login-error");
  err.textContent = "";
  try {
    await api("/api/login", { method: "POST",
      body: JSON.stringify({ password: document.getElementById("password").value }) });
    document.getElementById("login").hidden = true;
    route();
  } catch (ex) { err.textContent = ex.message; }
});
document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

// ------------------------------------------------------------------ grafik
function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.font = "11px system-ui, sans-serif";
  const style = getComputedStyle(document.body);
  return { ctx, w, h, muted: style.getPropertyValue("--muted"), line: style.getPropertyValue("--line") };
}

function axes(c, pad, ymax, yTicks = 4) {
  const { ctx, w, h } = c;
  ctx.strokeStyle = c.line; ctx.fillStyle = c.muted; ctx.textAlign = "right";
  for (let i = 0; i <= yTicks; i++) {
    const v = ymax * i / yTicks, y = h - pad.b - (h - pad.t - pad.b) * i / yTicks;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText(fmt(v), pad.l - 4, y + 3);
  }
}

// sampel live per (kamera|metric) -> region -> menit -> [nilai]; bertahan antar-reload grafik
const liveTails = {};
const aggregate = (vals, agg) => agg === "max" ? Math.max(...vals) : agg === "last" ? vals[vals.length - 1]
  : agg === "sum" ? vals.reduce((a, b) => a + b, 0) : vals.reduce((a, b) => a + b, 0) / vals.length;

// gap: jarak (detik) di atas mana garis diputus — tidak ada data = tidak ada garis
function lineChart(canvas, series, gap = Infinity) {   // series: [{name, xs, ys, color}]
  const c = setupCanvas(canvas), { ctx, w, h } = c, pad = { l: 36, r: 8, t: 8, b: 20 };
  const xs = series.flatMap(s => s.xs), ys = series.flatMap(s => s.ys);
  if (!xs.length) { ctx.fillStyle = c.muted; ctx.fillText("Belum ada data", w / 2, h / 2); return; }
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1, ymax = Math.max(1, ...ys) * 1.1;
  axes(c, pad, ymax);
  const X = x => pad.l + (w - pad.l - pad.r) * (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0));
  const Y = y => h - pad.b - (h - pad.t - pad.b) * y / ymax;
  ctx.fillStyle = c.muted;
  for (let i = 0; i <= 4; i++) {
    const x = x0 + (x1 - x0) * i / 4;
    ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center";   // label tepi tidak terpotong
    ctx.fillText(new Date(x * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }),
      i === 0 ? pad.l : i === 4 ? w - pad.r : X(x), h - 5);
  }
  for (const s of series) {
    ctx.strokeStyle = s.color; ctx.fillStyle = s.color; ctx.lineWidth = 2; ctx.beginPath();
    s.xs.forEach((x, i) => {
      const brk = i === 0 || x - s.xs[i - 1] > gap;
      (brk ? ctx.moveTo : ctx.lineTo).call(ctx, X(x), Y(s.ys[i]));
      // titik tanpa tetangga (dikelilingi celah) tetap terlihat sebagai kotak kecil
      const lone = brk && (i === s.xs.length - 1 || s.xs[i + 1] - x > gap);
      if (lone) ctx.fillRect(X(x) - 3, Y(s.ys[i]) - 3, 6, 6);
    });
    ctx.stroke();
  }
}

function barChart(canvas, values, color = PALETTE[1]) {   // 24 nilai: jam 00–23
  const c = setupCanvas(canvas), { ctx, w, h } = c, pad = { l: 36, r: 8, t: 8, b: 20 };
  const ymax = Math.max(1, ...values) * 1.1;
  axes(c, pad, ymax);
  const bw = (w - pad.l - pad.r) / values.length;
  values.forEach((v, i) => {
    const bh = (h - pad.t - pad.b) * v / ymax;
    ctx.fillStyle = color;
    ctx.fillRect(pad.l + i * bw + 2, h - pad.b - bh, bw - 4, bh);
    if (i % 2 === 0) {
      ctx.fillStyle = c.muted; ctx.textAlign = "center";
      ctx.fillText(String(i).padStart(2, "0"), pad.l + i * bw + bw / 2, h - 5);
    }
  });
}

// ------------------------------------------------------------------ halaman: ringkasan
function stateBadge(cam) {
  const s = cam.error ? "error" : cam.running ? cam.state : "berhenti";
  return el("span", { class: `state ${s}` }, cam.error ? "error" : s);
}

async function pageHome() {
  const grid = el("div", { class: "grid" });
  view.replaceChildren(el("div", { class: "row space" }, el("h2", {}, "Ringkasan kamera"),
    el("span", { class: "muted" }, "Snapshot diperbarui tiap 2 detik")), grid);
  const cards = {};
  async function refresh() {
    const cams = await api("/api/cameras");
    if (!cams.length) { grid.replaceChildren(el("p", { class: "muted" }, "Belum ada kamera.")); return; }
    for (const cam of cams) {
      let card = cards[cam.id];
      if (!card) {
        card = cards[cam.id] = {
          img: el("img", { class: "frame", alt: cam.name }),
          empty: el("div", { class: "frame empty" }, "kamera tidak berjalan"),
          badge: el("span"), info: el("div", { class: "kv" }), title: el("h3", {}, cam.name),
        };
        grid.append(el("a", { class: "card cam", href: `#/cam/${encodeURIComponent(cam.id)}` },
          card.img, card.empty, el("div", { class: "row space" }, card.title, card.badge), card.info));
      }
      card.badge.replaceChildren(stateBadge(cam));
      card.img.hidden = !cam.has_frame; card.empty.hidden = cam.has_frame;
      if (cam.has_frame) card.img.src = `/api/cameras/${encodeURIComponent(cam.id)}/snapshot.jpg?w=640&t=${Date.now()}`;
      const rows = [["Modul", cam.module_name],
        ["FPS proses", `${fmt(cam.proc_fps)} / ${fmt(cam.stream_fps)}`],
        ["Detect · modul", `${fmt(cam.detect_ms, 0)} ms · ${fmt(cam.module_ms, 0)} ms`],
        ...Object.entries(cam.metrics || {}),
        ["Alert belum dibaca", cam.unread_alerts]];
      if (cam.error) rows.push(["Error", cam.error]);
      card.info.replaceChildren(...rows.flatMap(([k, v]) => [el("span", { class: "muted" }, k), el("span", {}, v)]));
    }
  }
  await refresh();
  timers.push(setInterval(() => refresh().catch(() => {}), 2000));
}

// ------------------------------------------------------------------ halaman: detail kamera
async function pageCamera(camId) {
  const id = encodeURIComponent(camId);
  const live = el("img", { class: "frame", "data-live": "1", alt: "live" });
  const mode = el("select", {}, el("option", { value: "live" }, "Live (MJPEG ±10 fps)"),
    el("option", { value: "snap" }, "Snapshot (hemat, tiap 2 detik)"));
  const range = el("select", {}, el("option", { value: 1 }, "Hari ini"),
    el("option", { value: 7 }, "7 hari"), el("option", { value: 30 }, "30 hari"));
  const title = el("h2", {}, camId), info = el("span", { class: "muted" });
  const widgets = el("div", { class: "widgets" });
  view.replaceChildren(el("div", { class: "row space" }, title, el("a", { href: "#/" }, "← Ringkasan")),
    el("div", { class: "card" }, el("div", { class: "row space" }, mode, info), live),
    el("div", { class: "row" }, el("h3", {}, "Analitik"), range), widgets);

  let snapTimer = null;
  function setMode() {
    clearInterval(snapTimer);
    if (mode.value === "live") live.src = `/api/cameras/${id}/live.mjpg?fps=10&t=${Date.now()}`;
    else {
      const tick = () => { live.src = `/api/cameras/${id}/snapshot.jpg?t=${Date.now()}`; };
      tick(); snapTimer = setInterval(tick, 2000); timers.push(snapTimer);
    }
  }
  live.addEventListener("error", () => { info.textContent = "Kamera tidak sedang berjalan."; });
  live.addEventListener("load", () => { info.textContent = ""; });
  mode.addEventListener("change", setMode);
  setMode();

  async function loadWidgets() {
    const d = await api(`/api/cameras/${id}/dashboard?days=${range.value}`);
    title.textContent = d.camera.name;
    info.textContent = `Modul ${d.module.name} ${d.module.version}`;
    const reg = r => d.regions[r] || r || "kamera";
    widgets.replaceChildren(...d.widgets.map(w => renderWidget(w, d, reg, id, range.value)).filter(Boolean));
    widgets.querySelectorAll("canvas").forEach(cv => cv._draw && cv._draw());
  }
  range.addEventListener("change", () => loadWidgets().catch(e => { widgets.textContent = e.message; }));
  await loadWidgets().catch(e => { widgets.textContent = e.message; });
  timers.push(setInterval(() => loadWidgets().catch(() => {}), 15000));   // muat ulang penuh: tiap 15 s

  // KPI "sekarang" & ujung grafik: tiap 2 s dari endpoint ringan, tanpa memuat ulang semua
  async function updateKpis() {
    const lv = await api(`/api/cameras/${id}/live`);
    for (const [k, r, v] of lv.live_metrics) {
      const key = `${k}|${r || ""}`;
      const big = widgets.querySelector(`[data-kpi="${CSS.escape(key)}"]`);
      if (big) big.textContent = fmt(v) + (big.dataset.unit || "");
      const when = widgets.querySelector(`[data-kpi-when="${CSS.escape(key)}"]`);
      if (when) when.textContent = when.textContent.replace(/(terakhir, pukul [\d.:]+|sekarang)$/, "sekarang");
    }
    // ujung grafik: tambahkan sampel ke menit berjalan (jam server), lalu gambar ulang
    const minute = Math.floor(lv.ts / 60) * 60;
    widgets.querySelectorAll("canvas[data-live-metric]").forEach(cv => {
      for (const [k, r, v] of lv.live_metrics) {
        if (k !== cv.dataset.liveMetric) continue;
        const per = cv._tails[r || ""] ||= {};
        (per[minute] ||= []).push(v);
        for (const mm of Object.keys(per)) if (Number(mm) < lv.ts - 300) delete per[mm];
      }
      cv._draw();
    });
  }
  timers.push(setInterval(() => updateKpis().catch(() => {}), 2000));
}

function renderWidget(w, d, reg, id, days) {
  const spec = w.spec, m = spec.metric ? d.metrics[spec.metric] : null;
  const title = spec.title || (m ? m.label : spec.event ? d.events[spec.event] : spec.type);
  const unit = m && m.unit ? ` ${m.unit}` : "";
  if (spec.type === "kpi" && m) {
    // angka besar = keadaan SEKARANG (live) / terakhir tercatat; ringkasan rentang di bawahnya
    const vals = w.values || {}, now = w.now || {}, latest = w.latest || {}, mx = w.max || {};
    const regions = [...new Set([...Object.keys(vals), ...Object.keys(now)])].sort();
    const rangeText = Number(days) === 1 ? "hari ini" : `${days} hari`;
    return el("div", { class: "card" }, el("h3", {}, title),
      regions.length ? regions.map(r => {
        const [big, when] = r in now ? [now[r], "sekarang"]
          : r in latest ? [latest[r][1], `terakhir, pukul ${new Date(latest[r][0] * 1000)
            .toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}`]
          : [vals[r], m.agg_label];
        let summary = r in vals ? `${m.agg_label} ${fmt(vals[r], 2)}${unit}` : "belum ada data tersimpan";
        if (r in mx) summary += ` · maks ${fmt(mx[r])}${unit}`;
        const key = `${spec.metric}|${r}`;     // dipakai updateKpis() untuk pembaruan live
        return el("div", {}, el("div", { class: "kpi", "data-kpi": key, "data-unit": unit }, fmt(big) + unit),
          el("div", { class: "muted" }, el("span", { "data-kpi-when": key }, r ? `${reg(r)} · ${when}` : when),
            ` · ${rangeText}: ${summary}`));
      }) : el("div", { class: "kpi" }, "–"));
  }
  if (spec.type === "timeseries" && m) {
    const cv = el("canvas"), entries = Object.entries(w.series || {});
    // ujung live: menit yang sedang berjalan, diringkas dengan agg yang sama seperti database
    const tails = liveTails[`${id}|${spec.metric}`] ||= {};
    if ((w.bucket_s || 60) === 60) { cv.dataset.liveMetric = spec.metric; cv._tails = tails; cv._agg = m.agg; }
    cv._draw = () => lineChart(cv, entries.map(([r, [xs, ys]], i) => {
      const last = xs.length ? xs[xs.length - 1] : -Infinity;
      const tail = Object.entries((w.bucket_s || 60) === 60 ? tails[r] || {} : {})
        .map(([mm, vals]) => [Number(mm), aggregate(vals, m.agg)]).filter(([x]) => x > last)
        .sort((a, b) => a[0] - b[0]);
      return { name: reg(r), xs: xs.concat(tail.map(t => t[0])), ys: ys.concat(tail.map(t => t[1])),
        color: PALETTE[i % PALETTE.length] };
    }), 2 * (w.bucket_s || 60));
    return el("div", { class: "card wide" }, el("h3", {}, title + (m.unit ? ` (${m.unit})` : "")), cv,
      el("div", { class: "legend" }, entries.map(([r], i) =>
        el("span", {}, el("i", { style: `background:${PALETTE[i % PALETTE.length]}` }), reg(r)))));
  }
  if (spec.type === "hourly") {
    const cv = el("canvas");
    cv._draw = () => barChart(cv, w.bars || []);
    return el("div", { class: "card" }, el("h3", {}, `${title} (jam 00–23)`), cv);
  }
  if (spec.type === "events") {
    const rows = w.rows || [];
    return el("div", { class: "card wide" }, el("h3", {}, `${title || "Event"} — ${rows.length} terbaru`),
      el("table", {}, el("tr", {}, ["Waktu", "Event", "Zona", "Track", "Data"].map(h => el("th", {}, h))),
        rows.slice(0, 50).map(e => el("tr", {}, el("td", {}, fmtTime(e.ts)), el("td", {}, d.events[e.type] || e.type),
          el("td", {}, reg(e.region_id)), el("td", {}, e.track_id ?? ""),
          el("td", {}, Object.entries(e.data).map(([k, v]) => `${k}=${typeof v === "number" ? fmt(v) : v}`).join(", "))))));
  }
  if (spec.type === "heatmap") {
    return el("div", { class: "card" }, el("h3", {}, spec.title || "Heatmap"),
      w.has_heat ? el("img", { class: "frame", src: `/api/cameras/${id}/heatmap.png?days=${days}&t=${Date.now()}` })
        : el("p", { class: "muted" }, "Belum ada data heatmap pada rentang ini."));
  }
  if (spec.type === "health") {
    const [on, total] = w.uptime, cv = el("canvas"), rows = w.health || [];
    cv._draw = () => lineChart(cv, [["proc_fps", "FPS proses"], ["detect_ms", "detect (ms)"], ["module_ms", "modul (ms)"]]
      .map(([k, name], i) => ({ name, xs: rows.map(r => r.minute_ts), ys: rows.map(r => r[k] || 0), color: PALETTE[i] })), 120);
    const counts = Object.entries(w.counts).map(([t, n]) => `${d.events[t] || t}: ${n}`).join(", ") || "–";
    return el("div", { class: "card wide" }, el("h3", {}, "Kinerja & kesehatan kamera"),
      el("p", { class: "muted" }, total ? `Uptime ${fmt(100 * on / total)}% dari ${total} menit · Event: ${counts}`
        : `Belum ada data kesehatan · Event: ${counts}`), rows.length ? cv : null,
      rows.length ? el("div", { class: "legend" }, ["FPS proses", "detect (ms)", "modul (ms)"].map((n, i) =>
        el("span", {}, el("i", { style: `background:${PALETTE[i]}` }), n))) : null);
  }
  return null;
}

// ------------------------------------------------------------------ halaman: alert
async function pageAlerts() {
  const range = el("select", {}, [7, 1, 30, 90].map(n => el("option", { value: n }, n === 1 ? "Hari ini" : `${n} hari`)));
  const unread = el("input", { type: "checkbox", id: "unread" });
  const table = el("table");
  const ackAll = el("button", { class: "primary" }, "Tandai semua dibaca");
  view.replaceChildren(el("div", { class: "row space" }, el("h2", {}, "Alert"),
    el("div", { class: "row" }, range, el("label", { for: "unread" }, unread, " hanya belum dibaca"), ackAll)),
    el("div", { class: "card" }, table));
  async function refresh() {
    const d = await api(`/api/alerts?days=${range.value}&unread=${unread.checked}`);
    // replaceChildren() bawaan tidak meratakan array: baris harus di-spread
    table.replaceChildren(el("tr", {}, ["Waktu", "Kamera", "Aturan", "Tingkat", "Pesan", ""].map(h => el("th", {}, h))),
      ...(d.alerts.length ? d.alerts.map(a => el("tr", { class: a.acknowledged ? "" : "unread" },
        el("td", {}, fmtTime(a.ts)), el("td", {}, a.camera_name), el("td", {}, a.rule_id),
        el("td", { class: `sev-${a.severity}` }, a.severity), el("td", {}, a.message),
        el("td", {}, a.acknowledged ? "dibaca" : el("button", { onclick: async () => {
          await api("/api/alerts/ack", { method: "POST", body: JSON.stringify({ ids: [a.id] }) });
          refresh(); updateBadge(); } }, "Tandai dibaca"))))
        : [el("tr", {}, el("td", { colspan: 6, class: "muted" }, "Tidak ada alert."))]));
  }
  ackAll.addEventListener("click", async () => {
    await api("/api/alerts/ack", { method: "POST", body: JSON.stringify({ all: true }) });
    refresh(); updateBadge();
  });
  range.addEventListener("change", refresh); unread.addEventListener("change", refresh);
  await refresh();
  timers.push(setInterval(() => refresh().catch(() => {}), 10000));
}

// ------------------------------------------------------------------ halaman: laporan
async function pageReports() {
  const cams = await api("/api/cameras");
  const start = el("input", { type: "date", value: new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10) });
  const end = el("input", { type: "date", value: today() });
  const boxes = cams.map(c => el("label", {}, el("input", { type: "checkbox", value: c.id, checked: true }), " ", c.name));
  const query = () => {
    const ids = boxes.map(b => b.querySelector("input")).filter(i => i.checked).map(i => i.value);
    return `start=${start.value}&end=${end.value}&cams=${encodeURIComponent(ids.join(","))}`;
  };
  view.replaceChildren(el("h2", {}, "Laporan"), el("div", { class: "card" },
    el("div", { class: "row" }, "Periode", start, "s.d.", end),
    el("div", { class: "row" }, "Kamera:", boxes),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: () => { location.href = `/api/reports/pdf?${query()}`; } }, "Unduh PDF"),
      el("button", { onclick: () => { location.href = `/api/reports/csv?${query()}`; } }, "Unduh CSV (ZIP)"),
      el("button", { onclick: () => window.open(`/api/reports/html?${query()}`, "_blank") }, "Buka versi cetak"))));
}

// ------------------------------------------------------------------ router
async function updateBadge() {
  try {
    const d = await api("/api/alerts?days=1&unread=true");
    const b = document.getElementById("badge");
    b.hidden = !d.unread; b.textContent = d.unread;
  } catch (_) { /* belum login */ }
}

async function route() {
  clearTimers();
  const [, page, arg] = (location.hash || "#/").split("/");
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("active",
    a.dataset.nav === (page || "home") || (page === "cam" && a.dataset.nav === "home")));
  try {
    if (page === "cam" && arg) await pageCamera(decodeURIComponent(arg));
    else if (page === "alerts") await pageAlerts();
    else if (page === "reports") await pageReports();
    else await pageHome();
  } catch (e) { if (e.message !== "perlu login") view.replaceChildren(el("p", { class: "error" }, e.message)); }
}

window.addEventListener("hashchange", route);
window.addEventListener("resize", () => view.querySelectorAll("canvas").forEach(cv => cv._draw && cv._draw()));
(async () => {
  const h = await fetch("/api/health").then(r => r.json());
  document.getElementById("version").textContent = `· v${h.version}`;
  document.getElementById("logout").hidden = !h.auth_required;
  if (!h.logged_in) showLogin();
  else route();
  setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleTimeString("id-ID"); }, 1000);
  setInterval(updateBadge, 10000); updateBadge();
})();
