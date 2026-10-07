/* The Night Shift — city engine.
 * Every state change here is driven by a city event from the server's SSE
 * stream; each city event carries the agent trace line (agent_seq) that
 * caused it. No autonomous scripted drama: idle twinkle and clock only.
 */
"use strict";

const canvas = document.getElementById("city");
const ctx = canvas.getContext("2d");
ctx.imageSmoothingEnabled = false;

const S = 2;                 // one city pixel = S canvas pixels
const W = canvas.width / S;  // 480
const H = canvas.height / S; // 270
const GROUND_Y = 210;

// ------------------------------------------------------------------ scene
const LAYOUT = [
  { key: "power_plant", x: 8,   label: "AGENT",     sub: "the on-call agent" },
  { key: "house_a",     x: 88,  label: "REDIS",     sub: "cache" },
  { key: "town_hall",   x: 152, label: "CONTROL",   sub: "cluster control plane" },
  { key: "bank",        x: 248, label: "BANK",      sub: "payments-api" },
  { key: "house_b",     x: 352, label: "WEB",       sub: "frontend" },
  { key: "hospital",    x: 400, label: "DB",        sub: "postgres" },
];

let sheet = null, manifest = null;

let replayTimers = [];  // pending replay event timeouts (cancelled on reset)

// ------------------------------------------------------------- state
const state = {
  skyT: 0,              // 0 deep night -> 1 dawn
  skyTarget: 0,
  smokeOn: false,
  puffs: [],
  bubbles: [],          // {text, born}
  townhallGlow: 0,      // seconds remaining
  magnifier: 0,
  relightQueue: 0,      // bank windows relit so far
  relightAt: 0,
  repair: { x: 40, y: GROUND_Y, state: "idle", frame: 0, ft: 0, hammerLeft: 0 },
  banner: null,         // {kind:"alert"|"resolved"|"failed", text, link}
  runLabel: "",         // "LIVE run", "MOCK run", "replay"
  runId: null,
  pods: null,           // real cluster snapshot
  clusterOk: null,
  t: 0,
};

const bank = () => LAYOUT.find((b) => b.key === "bank");
const plant = () => LAYOUT.find((b) => b.key === "power_plant");

function windowStates(key, count) {
  if (!state.winLit[key]) state.winLit[key] = [];
  while (state.winLit[key].length < count)
    state.winLit[key].push({ lit: Math.random() > 0.35, phase: Math.random() * 6.28, glow: 0 });
  return state.winLit[key];
}
state.winLit = {};

// -------------------------------------------------------------- assets
Promise.all([
  new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.src = "assets/sprites.png"; }),
  fetch("assets/sprites.json").then((r) => r.json()),
]).then(([im, man]) => { sheet = im; manifest = man; requestAnimationFrame(loop); });

// ------------------------------------------------------------- helpers
function frame(name) { return manifest.frames[name]; }
function buildingFrame(key) { return manifest.buildings[key].frame; }
function windowRects(key) { return manifest.buildings[key].windows; }

function drawFromSheet(f, x, y, w, h) {
  ctx.drawImage(sheet, f.x, f.y, f.w, f.h, x * S, y * S, w * S, h * S);
}

function resetForRun(label) {
  for (const id of replayTimers) clearTimeout(id);
  replayTimers = [];
  state.skyT = 0; state.skyTarget = 0;
  state.smokeOn = false; state.puffs = []; state.bubbles = [];
  state.relightQueue = 0;
  state.repair = { x: plant().x + 20, y: GROUND_Y, state: "idle", frame: 0, ft: 0, hammerLeft: 0 };
  state.runLabel = label;
  for (const k of Object.keys(state.winLit))
    state.winLit[k].forEach((w) => { w.lit = Math.random() > 0.35; w.glow = 0; });
}

// ------------------------------------------------------- city events in
function applyCityEvent(ev) {
  const b = bank();
  switch (ev.city_event) {
    case "agent_wake":
      state.runId = ev.run_id;
      setStatusLine();
      break;
    case "incident_staged":
      state.banner = { kind: "staging", text: "staging a fresh incident: payments-api limit reset to 32Mi (real)" };
      setTimeout(() => { if (state.banner && state.banner.kind === "staging") state.banner = null; }, 6000);
      break;
    case "smoke_start":
      state.smokeOn = true;
      state.banner = { kind: "alert", text: "P1: payments-api failing — " + (ev.data.status || "unhealthy") };
      break;
    case "townhall_light":
      state.townhallGlow = 2.5;
      break;
    case "log_bubbles":
      break; // not emitted by mapper (individual log_bubble events are)
    case "log_bubble":
      state.bubbles.push({ text: String(ev.data.line || "").slice(0, 46), born: state.t });
      if (state.bubbles.length > 3) state.bubbles.shift();
      break;
    case "magnifier_ping":
      state.magnifier = 2.2;
      break;
    case "repair_walk":
      state.repair.state = "walking";
      state.repair.target = b.x + bldWidth("bank") / 2 - 10;
      break;
    case "hammer":
      state.repair.state = "hammering";
      state.repair.hammerLeft = 3;
      break;
    case "window_relight":
      state.smokeOn = false;
      state.relightQueue = windowRects("bank").length;
      state.relightAt = state.t;
      break;
    case "sky_dawn":
      state.skyTarget = 1;
      break;
    case "resolved": {
      const d = ev.data || {};
      const mode = String(d.spend_usd) !== "undefined" ? (state.runLabel.includes("LIVE") ? "LIVE" : "MOCK") : "";
      state.banner = {
        kind: "resolved",
        text: `RESOLVED in ${d.duration_s ?? "?"}s · ${d.tool_calls ?? "?"} tool calls · $${(d.spend_usd ?? 0).toFixed(4)}`,
        link: `api/runs/${ev.run_id}/trace`,
      };
      break;
    }
    case "resolved_failed":
      state.banner = {
        kind: "failed",
        text: "run ended without confirmed recovery" + (ev.data && ev.data.error ? ` — ${String(ev.data.error).slice(0, 80)}` : ""),
        link: `api/runs/${ev.run_id}/trace`,
      };
      state.smokeOn = false;
      break;
    case "cluster_down":
      state.clusterOk = false;
      state.smokeOn = false;
      break;
    case "guard_denied":
      state.bubbles.push({ text: "GUARD DENIED — tool layer refused", born: state.t, denied: true });
      if (state.bubbles.length > 3) state.bubbles.shift();
      break;
  }
}

function bldWidth(key) { return buildingFrame(key).w; }

// ------------------------------------------------------------- SSE wiring
let currentES = null;   // only one live stream at a time
let replayMode = false;
let replayEvents = [];

function follow(runId) {
  state.runId = runId;
  if (currentES) { currentES.close(); currentES = null; }
  replayMode = false;
  replayEvents = [];
  const es = new EventSource(`api/events/${runId}`);
  currentES = es;
  es.addEventListener("meta", (m) => {
    const meta = JSON.parse(m.data);
    replayMode = meta.replay;
    document.getElementById("replayTag").classList.toggle("hidden", !replayMode);
  });
  es.addEventListener("city", (m) => {
    const ev = JSON.parse(m.data);
    if (replayMode) { replayEvents.push(ev); return; }
    applyCityEvent(ev);  // live: apply the instant it happens
  });
  es.addEventListener("done", () => {
    es.close();
    if (currentES === es) currentES = null;
    if (replayMode && replayEvents.length) {
      // archived run: re-render compressed, honestly labeled as replay
      resetForRun("replay");
      let at = 0;
      let prev = 0;
      for (const ev of replayEvents) {
        const gap = Math.min((ev.elapsed_ms - prev) / 8, 1500);
        prev = ev.elapsed_ms;
        at += gap;
        replayTimers.push(setTimeout(() => applyCityEvent(ev), at));
      }
    }
    replayEvents = [];
  });
  es.onerror = () => es.close();
}

async function startRun() {
  const btn = document.getElementById("runBtn");
  btn.disabled = true;
  try {
    const res = await fetch("api/run", { method: "POST" });
    const data = await res.json();
    if (!res.ok) { setStatusLine(data.error); return; }
    resetForRun("live");
    state.banner = null;
    document.getElementById("replayTag").classList.add("hidden");
    follow(data.run_id);
  } catch (e) {
    setStatusLine("could not reach the server");
  } finally {
    setTimeout(() => { btn.disabled = false; }, 3000);
  }
}

// ------------------------------------------------------------- statusline
let statusNote = "";
function setStatusLine(note) {
  statusNote = note || "";
  renderStatusLine();
}
function renderStatusLine() {
  const el = document.getElementById("statusline");
  const pods = state.pods || {};
  const names = Object.keys(pods);
  const up = names.filter((n) => pods[n] === "Running").length;
  const parts = [];
  parts.push(`cluster: <b class="${state.clusterOk === false ? "down" : "up"}">${state.clusterOk === false ? "down" : "ok"}</b>`);
  if (names.length) {
    parts.push(`city: <b>${up}/${names.length}</b> services up`);
    const p = pods["payments-api"];
    if (p && p !== "Running")
      parts.push(`<span class="down">payments-api: ${p}</span>`);
  }
  parts.push(`agent: <b>${state.runLabel || "idle"}</b>`);
  if (statusNote) parts.push(`<span class="down">${statusNote}</span>`);
  el.innerHTML = parts.join(" · ");
}

async function pollStatus() {
  try {
    const r = await fetch("api/status");
    const d = await r.json();
    state.pods = d.pods || {};
    state.clusterOk = !!d.cluster_ok;
    renderStatusLine();
    return d;
  } catch (e) {
    state.clusterOk = false;
    renderStatusLine();
    return null;
  }
}

// ------------------------------------------------------------------ render
function lerp(a, b, t) { return a + (b - a) * t; }
function mixColor(c1, c2, t) {
  const p = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const a = p(c1), b2 = p(c2);
  return `rgb(${Math.round(lerp(a[0], b2[0], t))},${Math.round(lerp(a[1], b2[1], t))},${Math.round(lerp(a[2], b2[2], t))})`;
}

const stars = Array.from({ length: 70 }, () => ({
  x: Math.random() * W, y: Math.random() * 130, ph: Math.random() * 6.28, s: Math.random() < 0.2 ? 2 : 1,
}));

function drawSky() {
  const t = state.skyT;
  const g = ctx.createLinearGradient(0, 0, 0, H * S);
  g.addColorStop(0, mixColor("#04060f", "#5a7fb5", t));
  g.addColorStop(0.7, mixColor("#0a1024", "#c98a5a", t));
  g.addColorStop(1, mixColor("#141d33", "#f2a65e", t));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W * S, H * S);
  // stars
  ctx.globalAlpha = 0.9 * (1 - t);
  for (const st of stars) {
    const tw = 0.55 + 0.45 * Math.sin(state.t * 1.7 + st.ph);
    ctx.globalAlpha = (0.35 + 0.6 * tw) * (1 - t);
    ctx.fillStyle = "#cfd8ea";
    ctx.fillRect(st.x * S, st.y * S, st.s * S, st.s * S);
  }
  ctx.globalAlpha = 1;
}

function drawGround() {
  ctx.fillStyle = mixColor("#0a0e18", "#2c3550", state.skyT);
  ctx.fillRect(0, GROUND_Y * S, W * S, (H - GROUND_Y) * S);
  ctx.fillStyle = mixColor("#131a2a", "#3d4a6b", state.skyT);
  ctx.fillRect(0, GROUND_Y * S, W * S, S);
}

function drawBuilding(entry) {
  const f = buildingFrame(entry.key);
  const y = GROUND_Y - f.h;
  // subtle silhouette glow when lit
  drawFromSheet(f, entry.x, y, f.w, f.h);
  // windows
  const wins = windowRects(entry.key);
  const st = windowStates(entry.key, wins.length);
  wins.forEach((wr, i) => {
    const s = st[i];
    let alpha = s.lit ? 0.55 + 0.35 * Math.sin(state.t * 1.2 + s.phase) : 0.06;
    if (entry.key === "bank" && state.smokeOn) {
      alpha = s.lit ? (Math.random() < 0.12 ? 0.5 : 0.05) : 0.04; // flicker while failing
    }
    if (s.glow > 0) { alpha = 0.9; s.glow -= 1 / 60; }
    ctx.fillStyle = s.lit ? "#f5ee8c" : "#10141f";
    ctx.globalAlpha = alpha;
    ctx.fillRect((entry.x + wr.x) * S, (y + wr.y) * S, wr.w * S, wr.h * S);
    ctx.globalAlpha = 1;
  });
  if (entry.key === "bank" && state.relightQueue > 0) {
    const done = Math.floor((state.t - state.relightAt) / 0.3);
    for (let i = 0; i < Math.min(done, state.relightQueue); i++) {
      const s = st[i];
      if (!s.lit) { s.lit = true; s.glow = 1.2; }
    }
    if (done >= state.relightQueue) state.relightQueue = 0;
  }
  // label + status dot (dot color = REAL pod state from /api/status)
  ctx.font = `${5 * S}px monospace`;
  ctx.fillStyle = state.skyT > 0.5 ? "#39466b" : "#6a7691";
  ctx.fillText(entry.label, entry.x * S, (GROUND_Y + 14) * S);
  if (state.pods) {
    const podKey = { bank: "payments-api", hospital: "postgres", house_a: "redis", house_b: "web" }[entry.key];
    const ok = podKey ? state.pods[podKey] === "Running" : state.clusterOk === true;
    ctx.fillStyle = ok ? "#63c74d" : "#ff5d4a";
    ctx.fillRect((entry.x + 2) * S, (GROUND_Y + 17) * S, 3 * S, 3 * S);
  }
}

function drawTownhallGlow() {
  if (state.townhallGlow <= 0) return;
  const e = LAYOUT.find((b) => b.key === "town_hall");
  const f = buildingFrame("town_hall");
  ctx.fillStyle = "rgba(245, 238, 140, 0.18)";
  ctx.fillRect((e.x - 4) * S, (GROUND_Y - f.h - 6) * S, (f.w + 8) * S, (f.h + 10) * S);
  state.townhallGlow -= 1 / 60;
}

function drawSmoke(dt) {
  const b = bank();
  const f = buildingFrame("bank");
  if (state.smokeOn && Math.random() < dt * 6)
    state.puffs.push({ x: b.x + f.w * 0.62 + Math.random() * 14, y: GROUND_Y - f.h, v: 8 + Math.random() * 8, ph: Math.random() * 3 });
  const fr = [frame("smoke0"), frame("smoke1"), frame("smoke2")];
  state.puffs = state.puffs.filter((p) => p.y > GROUND_Y - f.h - 46);
  for (const p of state.puffs) {
    p.y -= p.v * dt;
    p.x += Math.sin(state.t * 2 + p.ph) * 0.2;
    const age = (GROUND_Y - f.h - p.y) / 46;
    ctx.globalAlpha = Math.max(0, 0.75 - age * 0.75);
    const fi = fr[Math.min(2, Math.floor(age * 3))];
    drawFromSheet(fi, p.x, p.y, fi.w, fi.h);
    ctx.globalAlpha = 1;
  }
}

function drawMagnifier() {
  if (state.magnifier <= 0) return;
  const b = bank();
  const f = buildingFrame("bank");
  const cx = (b.x + f.w / 2) * S;
  const cy = (GROUND_Y - f.h / 2) * S;
  const r = (2.2 - state.magnifier) * 26;
  ctx.strokeStyle = `rgba(159, 211, 255, ${state.magnifier / 2.2})`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, Math.max(4, r % 40), 0, 6.28);
  ctx.stroke();
  const m = frame("magnifier");
  drawFromSheet(m, b.x + f.w / 2 + 8, GROUND_Y - f.h / 2 - 6, m.w, m.h);
  state.magnifier -= 1 / 60;
}

function drawRepair(dt) {
  const r = state.repair;
  if (r.state === "walking") {
    r.x += 34 * dt;
    r.ft += dt;
    if (r.x >= r.target) { r.x = r.target; r.state = "hammering"; r.hammerLeft = 3; r.ft = 0; }
  } else if (r.state === "hammering") {
    r.ft += dt;
    if (r.ft > 0.9 && r.hammerLeft > 0) { r.hammerLeft -= 1; r.ft = 0; }
    if (r.hammerLeft <= 0 && r.ft > 0.9) r.state = "resting";
  } else if (r.state === "idle") {
    return;
  }
  let fname;
  if (r.state === "walking") fname = `walk${Math.floor(r.ft * 8) % 4}`;
  else if (r.state === "hammering") fname = `hammer${Math.floor(r.ft * 6) % 2}`;
  else fname = "walk0";
  const f = frame(`repair_${fname}`);
  drawFromSheet(f, r.x, GROUND_Y - 18, f.w, f.h);
}

function drawBubbles() {
  ctx.font = `${6 * S}px monospace`;
  const b = bank();
  const f = buildingFrame("bank");
  state.bubbles = state.bubbles.filter((bu) => state.t - bu.born < 4.5);
  state.bubbles.slice(-3).forEach((bu, i) => {
    const age = state.t - bu.born;
    const alpha = Math.max(0, Math.min(1, 4.5 - age));
    const wpx = ctx.measureText(bu.text).width + 10;
    const bx = b.x * S + (f.w * S) / 2 - wpx / 2;
    const by = (GROUND_Y - f.h - 26 - i * 16) * S;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = bu.denied ? "#2a0f0c" : "#101725";
    ctx.strokeStyle = bu.denied ? "#ff5d4a" : "#3d4a6b";
    ctx.lineWidth = 1;
    ctx.fillRect(bx, by, wpx, 13 * S / 2 + 6);
    ctx.strokeRect(bx, by, wpx, 13 * S / 2 + 6);
    ctx.fillStyle = bu.denied ? "#ffb4a8" : "#d6d9e0";
    ctx.fillText(bu.text, bx + 5, by + 11);
    ctx.globalAlpha = 1;
  });
}

function drawBanner() {
  const el = document.getElementById("alertBanner");
  const rs = document.getElementById("resolveBanner");
  const bn = state.banner;
  if (!bn || bn.kind === "staging") {
    el.classList.add("hidden"); rs.classList.add("hidden");
    if (bn && bn.kind === "staging") {
      el.classList.remove("hidden");
      document.getElementById("alertText").textContent = bn.text;
      document.querySelector("#alertBanner .pulse").style.background = "#ffd75e";
    }
    return;
  }
  document.querySelector("#alertBanner .pulse").style.background = "";
  if (bn.kind === "alert") {
    rs.classList.add("hidden"); el.classList.remove("hidden");
    document.getElementById("alertText").textContent = bn.text;
  } else {
    el.classList.add("hidden"); rs.classList.remove("hidden");
    document.getElementById("resolveText").textContent = bn.text + (bn.kind === "failed" ? "" : "");
    const link = document.getElementById("traceLink");
    link.href = bn.link || "#";
    link.style.display = bn.link ? "" : "none";
  }
}

let last = 0;
function loop(ts) {
  const dt = Math.min(0.05, (ts - last) / 1000 || 0.016);
  last = ts;
  state.t += dt;
  state.skyT += (state.skyTarget - state.skyT) * Math.min(1, dt * 0.35);
  drawSky();
  drawGround();
  for (const e of LAYOUT) drawBuilding(e);
  drawTownhallGlow();
  drawSmoke(dt);
  drawMagnifier();
  drawRepair(dt);
  drawBubbles();
  drawBanner();
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ boot
window.state = state;  // exposed for tests/screenshots (read-only inspection)
document.getElementById("runBtn").addEventListener("click", startRun);
pollStatus().then((d) => {
  if (d && d.latest_run && !d.active_run) follow(d.latest_run);      // auto-replay last shift
  else if (d && d.active_run) { resetForRun("live"); follow(d.active_run); }
});
setInterval(pollStatus, 8000);