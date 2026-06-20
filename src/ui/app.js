// Wrapper UI controller: talks to the Python js_api bridge + the relay WebSocket.
let API = null;          // window.pywebview.api once ready
let CFG = null;
let relay = null;
const panels = {};       // clientKey -> {col, body, jump, dot, cnt, pinned, n, cur:{userId->el}}
const DEFAULT_AV = "https://cdn.discordapp.com/embed/avatars/0.png";
const CLIENT_LABELS = { "discordptb.exe": "Discord PTB", "discord.exe": "Discord",
                        "discordcanary.exe": "Discord Canary", "discorddevelopment.exe": "Discord Dev" };
const CLIENT_COLORS = { "discordptb.exe": "#3ba55d", "discord.exe": "#5865f2",
                        "discordcanary.exe": "#faa61a", "discorddevelopment.exe": "#eb459e" };
const clientLabel = (c) => CLIENT_LABELS[(c || "").toLowerCase()] || (c || "Unknown");
const EVENT_LABEL = { joined: "joined the channel", left: "left the channel", muted: "muted",
                      unmuted: "unmuted", deafened: "deafened", undeafened: "undeafened",
                      video_on: "turned camera on", video_off: "turned camera off",
                      stream_on: "started streaming", stream_off: "stopped streaming" };

const $ = (id) => document.getElementById(id);

// stable per-user color from id
function colorFor(id) {
  let h = 0; const s = String(id || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 65% 72%)`;
}

// ---------- transcript direction (newest at bottom by default, flippable) ----------
const newestTop = () => !!(CFG && CFG.ui && CFG.ui.newest_at_top);
const jumpLabel = () => "Jump to latest " + (newestTop() ? "↑" : "↓");
function placeRow(p, el) {                 // insert at the "newest" end
  if (newestTop()) p.body.insertBefore(el, p.body.firstChild);
  else p.body.appendChild(el);
}

// ---------- per-client overlay + own-voice toggles ----------
function injectFor(exe) {
  const v = CFG && CFG.inject_overlay;
  if (v && typeof v === "object") return v[exe] !== false;
  if (typeof v === "boolean") return v;
  return true;
}
function setInject(exe, on) {
  let v = CFG.inject_overlay;
  if (!v || typeof v !== "object") v = {};
  v = Object.assign({}, v); v[exe] = on; CFG.inject_overlay = v;
  API.save_config(CFG); toast("Overlay " + (on ? "on" : "off") + " — restart engine to apply", false);
}
function selfFor(exe) {
  const cl = ((CFG && CFG.self_transcribe) || {}).clients || {};
  return cl[exe] !== false;
}
function setSelf(exe, on) {
  const s = Object.assign({}, CFG.self_transcribe);
  s.clients = Object.assign({}, s.clients || {}); s.clients[exe] = on; CFG.self_transcribe = s;
  API.save_config(CFG); toast("Own-voice " + (on ? "on" : "off") + " for " + exe + " — restart engine", false);
}
function makeSwitch(checked, onChange) {
  const lab = document.createElement("label"); lab.className = "switch";
  lab.innerHTML = `<input type="checkbox" ${checked ? "checked" : ""}><span class="sl"></span>`;
  lab.querySelector("input").addEventListener("change", (e) => onChange(e.target.checked));
  return lab;
}
function renderToggleList(boxId, isOn, set) {
  const box = $(boxId);
  if (!clientList.length) { box.innerHTML = '<div class="hint">No Discord clients detected yet.</div>'; return; }
  box.innerHTML = "";
  for (const c of clientList) {
    const row = document.createElement("div"); row.className = "toggrow";
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = c.folder;
    row.appendChild(nm);
    row.appendChild(makeSwitch(isOn(c.exe), (on) => set(c.exe, on)));
    box.appendChild(row);
  }
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll(".view").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $("v-" + t.dataset.v).classList.add("active");
}));

// ---------- config form ----------
let kwList = [];
const toHex = (s) => {
  s = String(s || "#f04747").trim();
  if (/^#[0-9a-fA-F]{3}$/.test(s)) s = "#" + s.slice(1).split("").map((c) => c + c).join("");
  return /^#[0-9a-fA-F]{6}$/.test(s) ? s.toLowerCase() : "#f04747";
};

function fillForm(c) {
  CFG = c;
  $("whisper_model").value = c.whisper_model;
  const g = c.gating || {};
  $("g_dbfs").value = g.min_rms_dbfs ?? -50;
  $("g_dbfs_v").textContent = $("g_dbfs").value;
  $("g_vad").checked = g.vad !== false;
  $("g_drop").value = (g.drop_phrases || []).join(", ");
  const a = c.alerts || {};
  kwList = (a.keywords || []).slice(); renderPills();
  $("a_sound").checked = a.sound !== false;
  $("a_highlight").value = toHex(a.highlight);
  const o = c.overlay || {};
  $("o_timeout").value = o.subtitle_timeout_ms ?? 8000;
  $("o_max").value = o.max_blocks ?? 6;
  $("o_fade").value = o.fade_start_count ?? 5;
  $("o_minop").value = o.min_fade_opacity ?? 0.25;
  $("o_logh").value = o.log_height ?? 300;
  $("ui_events").checked = c.voice_events !== false;
  const u = c.ui || {};
  $("ui_ts").checked = !!u.show_timestamps;
  $("ui_tsfmt").value = u.timestamp_format || "clock";
  $("ui_newtop").checked = !!u.newest_at_top;
  $("adv_lang").value = c.language || "";
  $("adv_beam").value = c.beam_size ?? 1;
  $("adv_device").value = c.device || "cuda";
  $("adv_compute").value = c.compute_type || "float16";
  $("adv_relay").value = c.relay_port ?? 8765;
  const s = c.self_transcribe || {};
  $("self_en").checked = !!s.enabled;
  $("self_unmute").checked = s.only_when_unmuted !== false;
  $("self_vad").checked = s.require_discord_speaking !== false;
  $("self_device").value = s.device == null ? "" : String(s.device);
}
$("g_dbfs").addEventListener("input", () => $("g_dbfs_v").textContent = $("g_dbfs").value);
$("ui_newtop").addEventListener("change", () => {
  if (!CFG.ui) CFG.ui = {};
  CFG.ui.newest_at_top = $("ui_newtop").checked;   // flip live (scheduleSave persists it)
  applyDirection();
});

function readForm() {
  const csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  return Object.assign({}, CFG, {
    whisper_model: $("whisper_model").value,
    voice_events: $("ui_events").checked,
    language: $("adv_lang").value.trim(),
    beam_size: parseInt($("adv_beam").value, 10) || 1,
    device: $("adv_device").value,
    compute_type: $("adv_compute").value,
    relay_port: parseInt($("adv_relay").value, 10) || 8765,
    gating: Object.assign({}, CFG.gating, {
      min_rms_dbfs: parseFloat($("g_dbfs").value),
      vad: $("g_vad").checked,
      drop_phrases: csv($("g_drop").value),
    }),
    alerts: Object.assign({}, CFG.alerts, {
      keywords: kwList.slice(),
      sound: $("a_sound").checked,
      highlight: $("a_highlight").value,
    }),
    overlay: Object.assign({}, CFG.overlay, {
      subtitle_timeout_ms: parseInt($("o_timeout").value, 10),
      max_blocks: parseInt($("o_max").value, 10),
      fade_start_count: parseInt($("o_fade").value, 10),
      min_fade_opacity: parseFloat($("o_minop").value),
      log_height: parseInt($("o_logh").value, 10) || 300,
    }),
    ui: Object.assign({}, CFG.ui, {
      show_timestamps: $("ui_ts").checked,
      timestamp_format: $("ui_tsfmt").value,
      newest_at_top: $("ui_newtop").checked,
    }),
    self_transcribe: Object.assign({}, CFG.self_transcribe, {
      enabled: $("self_en").checked,
      only_when_unmuted: $("self_unmute").checked,
      require_discord_speaking: $("self_vad").checked,
      device: $("self_device").value === "" ? null
              : (/^\d+$/.test($("self_device").value) ? parseInt($("self_device").value, 10) : $("self_device").value),
    }),
  });
}

// ---------- keyword pill editor ----------
function renderPills() {
  const box = $("kw"), input = $("kw-input");
  box.querySelectorAll(".pill-tag").forEach((p) => p.remove());
  kwList.forEach((k, i) => {
    const tag = document.createElement("span"); tag.className = "pill-tag";
    tag.innerHTML = `<b></b><span class="pill-x">×</span>`;
    tag.querySelector("b").textContent = k;
    tag.querySelector(".pill-x").onclick = () => { kwList.splice(i, 1); renderPills(); scheduleSave(); };
    box.insertBefore(tag, input);
  });
}
function addKw(v) {
  v = v.trim().replace(/,$/, "").trim();
  if (v && !kwList.some((k) => k.toLowerCase() === v.toLowerCase())) { kwList.push(v); renderPills(); scheduleSave(); }
}
function initPills() {
  const input = $("kw-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addKw(input.value); input.value = ""; }
    else if (e.key === "Backspace" && !input.value && kwList.length) { kwList.pop(); renderPills(); scheduleSave(); }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) { addKw(input.value); input.value = ""; } });
}

// ---------- auto-save + toast ----------
let saveTimer = null;
function toast(text, saving) {
  const t = $("toast"); $("toasttext").textContent = text;
  t.classList.toggle("saving", !!saving); t.classList.add("show");
  clearTimeout(toast._h);
  if (!saving) toast._h = setTimeout(() => t.classList.remove("show"), 1600);
}
function scheduleSave() {
  if (!API) return;
  toast("Saving…", true);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { const cfg = readForm(); await API.save_config(cfg); CFG = cfg; toast("Saved ✓", false); }
    catch (e) { toast("Save failed", false); }
  }, 450);
}
function initAutosave() {
  const v = $("v-settings");
  const onChange = (e) => {
    if (e.target.id === "kw-input") return;
    if (e.target.id === "adv_device") refreshGpu();
    scheduleSave();
  };
  v.addEventListener("input", onChange);
  v.addEventListener("change", onChange);
}
$("restartbtn").addEventListener("click", async () => {
  const b = $("restartbtn"); b.disabled = true; toast("Restarting engine…", true);
  try { await API.stop_backend(); await API.start_backend(); } catch (e) {}
  setTimeout(() => { refreshEngine(); connectRelay(); toast("Engine restarted ✓", false); b.disabled = false; }, 1300);
});

// ---------- clients ----------
let clientList = [];
let engineStatus = {};        // exe -> {hooked, cdp, streams, active, mapped} from the engine heartbeat

async function refreshClients() { clientList = await API.list_clients(); renderClients(); }

function renderClients() {
  const box = $("clients");
  if (!clientList.length) { box.innerHTML = '<div class="empty">No Discord clients found.</div>'; return; }
  box.innerHTML = "";
  for (const c of clientList) {
    const es = engineStatus[c.exe];
    const hooked = es && es.hooked, cdp = es && es.cdp;
    const streams = es ? es.streams : 0, mapped = es ? es.mapped : 0;
    let dot, label, tip;
    if (hooked) {
      dot = "good";
      label = `attached ✓ · ${streams} stream${streams === 1 ? "" : "s"}`;
      tip = cdp ? `Hooked + names resolving via CDP (${mapped} mapped).`
                : "Audio hooked, but NO debug port — names stay as “user …”. Use Restart w/ port.";
    } else if (c.live) {
      dot = "info"; label = "debug port ready";
      tip = "Debug port open. Will attach once you Start the engine and a call is active.";
    } else if (c.running) {
      dot = "warn"; label = "running — no debug port";
      tip = "Capture works but names won't resolve. Restart w/ port enables names (closes the current call).";
    } else {
      dot = "off"; label = "not running";
      tip = "Launch this client to capture it.";
    }
    const row = document.createElement("div");
    row.className = "clientrow"; row.title = tip;
    row.innerHTML = `<span class="cdot ${dot}"></span><span class="nm">${c.folder}</span><span class="st">${label}</span>`;
    const btn = document.createElement("button");
    btn.className = "sec";
    btn.textContent = c.live ? "Relaunch" : (c.running ? "Restart w/ port" : "Launch");
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "…";
      await API.ensure_client(c.folder, c.running && !c.live);
      setTimeout(refreshClients, 1500);
    };
    row.appendChild(btn);
    box.appendChild(row);
  }
  renderToggleList("overlay_clients", injectFor, setInject);
  renderToggleList("self_clients", selfFor, setSelf);
}

// ---------- engine start/stop ----------
async function refreshEngine() {
  const running = await API.backend_status();
  $("bdot").className = "dot " + (running ? "on" : "off");
  $("bstat").textContent = running ? "engine running" : "stopped";
  $("startbtn").disabled = running;
  $("stopbtn").disabled = !running;
}
$("startbtn").addEventListener("click", async () => {
  $("startbtn").disabled = true;
  showProgress({ active: true, done: false, pct: null, label: "Starting engine…" });
  await API.start_backend();
  setTimeout(refreshEngine, 800);
  setTimeout(connectRelay, 1500);
});
$("stopbtn").addEventListener("click", async () => {
  $("stopbtn").disabled = true;
  await API.stop_backend();
  setTimeout(refreshEngine, 500);
});

// ---------- first-run download / loading banner ----------
function showProgress(p) {
  const bar = $("firstrun");
  if (!bar) return;
  if (!p || !p.active || p.done) { bar.style.display = "none"; return; }
  bar.style.display = "block";
  $("fr-label").textContent = p.label || "Preparing…";
  const fill = $("fr-bar");
  if (typeof p.pct === "number") {
    fill.classList.remove("indet");
    fill.style.marginLeft = "0";
    fill.style.width = Math.max(0, Math.min(100, p.pct)) + "%";
    $("fr-pct").textContent = p.pct + "%";
  } else {
    fill.classList.add("indet");
    fill.style.width = "";
    $("fr-pct").textContent = "";
  }
}
async function pumpProgress() {
  if (API) {
    try { showProgress(await API.get_progress()); } catch (e) {}
  }
  setTimeout(pumpProgress, 600);
}

// ---------- console log ----------
async function pumpLog() {
  if (API) {
    try {
      const txt = await API.get_log();
      const el = $("log");
      const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
      el.textContent = txt;
      if (atBottom) el.scrollTop = el.scrollHeight;
    } catch (e) {}
  }
  setTimeout(pumpLog, 1000);
}

// ---------- relay (transcript) ----------
function connectRelay() {
  const port = (CFG && CFG.relay_port) || 8765;
  // Detach the old socket's handlers before closing it, otherwise its onclose would schedule
  // yet another reconnect and keep racing the socket we're about to open.
  try { if (relay) { relay.onclose = null; relay.onmessage = null; relay.close(); } } catch (e) {}
  const sock = new WebSocket("ws://127.0.0.1:" + port);
  relay = sock;
  sock.onopen = () => { if (relay !== sock) return; $("rdot").className = "dot on"; $("rstat").textContent = "relay"; };
  sock.onclose = () => {
    if (relay !== sock) return;                 // superseded by a newer socket — don't reconnect
    $("rdot").className = "dot off"; $("rstat").textContent = "relay off";
    setTimeout(connectRelay, 2000);
  };
  sock.onmessage = (ev) => {
    if (relay !== sock) return;
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "status") {
      $("activepill").textContent = (m.active || 0) + " stream" + (m.active === 1 ? "" : "s");
      if (m.clients) { engineStatus = m.clients; renderClients(); updateSpeaking(m.clients); }
    } else if (m.type === "transcript") {
      renderTranscript(m);
    } else if (m.type === "event") {
      renderEvent(m);
    } else if (m.type === "rename") {
      applyRename(m);
    }
  };
}

function panelFor(client) {
  const key = (client || "unknown").toLowerCase();
  if (panels[key]) return panels[key];
  const box = $("transcript");
  const empty = box.querySelector(".empty"); if (empty) empty.remove();

  const col = document.createElement("div"); col.className = "tcol";
  const head = document.createElement("div"); head.className = "tcol-h";
  const dot = document.createElement("span"); dot.className = "cdot";
  dot.style.background = CLIENT_COLORS[key] || "#5865f2";
  const title = document.createElement("span"); title.textContent = clientLabel(client);
  const spk = document.createElement("span"); spk.className = "cspk";   // live "N speaking" for THIS client
  const cnt = document.createElement("span"); cnt.className = "cnt"; cnt.textContent = "0";
  const clr = document.createElement("span"); clr.className = "tcol-clear"; clr.textContent = "clear";
  clr.title = "Clear this client's transcript";
  head.appendChild(dot); head.appendChild(title); head.appendChild(spk); head.appendChild(cnt); head.appendChild(clr);

  const body = document.createElement("div"); body.className = "tcol-body";
  const jump = document.createElement("button"); jump.className = "jump"; jump.textContent = jumpLabel();

  col.appendChild(head); col.appendChild(body); col.appendChild(jump);
  // keep columns ordered by label for stable layout
  const cols = Array.from(box.children);
  const after = cols.find((c) => c._label && c._label > clientLabel(client));
  if (after) box.insertBefore(col, after); else box.appendChild(col);
  col._label = clientLabel(client);

  // "pinned" = scrolled to the newest end (top when newest_at_top, else bottom)
  const p = { col, body, jump, cnt, spk, pinned: true, n: 0, cur: {}, lastAuto: 0, jt: null };
  clr.onclick = () => { body.innerHTML = ""; p.cur = {}; p.n = 0; cnt.textContent = "0"; };
  body.addEventListener("scroll", () => {
    if (Date.now() - p.lastAuto < 130) return;            // ignore our own auto-scroll -> no flicker
    const atEnd = newestTop() ? (body.scrollTop < 28)
                              : (body.scrollTop + body.clientHeight >= body.scrollHeight - 28);
    if (atEnd) { p.pinned = true; jump.style.display = "none"; if (p.jt) { clearTimeout(p.jt); p.jt = null; } }
    else { p.pinned = false; if (!p.jt) p.jt = setTimeout(() => { if (!p.pinned) jump.style.display = "block"; p.jt = null; }, 180); }
  });
  jump.addEventListener("click", () => {
    p.pinned = true; jump.style.display = "none"; p.lastAuto = Date.now();
    body.scrollTop = newestTop() ? 0 : body.scrollHeight;
  });
  panels[key] = p;
  return p;
}

function pinScroll(p) { if (p.pinned) { p.lastAuto = Date.now(); p.body.scrollTop = newestTop() ? 0 : p.body.scrollHeight; } }
function capLines(p) {                       // drop the OLDEST rows (opposite end from newest)
  while (p.body.children.length > 200) p.body.removeChild(newestTop() ? p.body.lastChild : p.body.firstChild);
}
// per-client live "N speaking" badge — each pane shows ONLY its own client's active streams
// (iterate panels, not the heartbeat, so a client that goes quiet/absent clears its own badge)
function updateSpeaking(clients) {
  for (const key in panels) {
    const p = panels[key];
    if (!p || !p.spk) continue;
    const a = (clients[key] && clients[key].active) || 0;
    p.spk.textContent = a ? a + " speaking" : "";
  }
}
// flip every panel's existing rows + scroll anchor when the direction setting changes
function applyDirection() {
  Object.values(panels).forEach((p) => {
    Array.from(p.body.children).reverse().forEach((r) => p.body.appendChild(r));
    if (p.jump) p.jump.textContent = jumpLabel();
    p.pinned = true; p.lastAuto = Date.now();
    p.body.scrollTop = newestTop() ? 0 : p.body.scrollHeight;
  });
}

function renderEvent(m) {
  if (CFG && CFG.voice_events === false) return;
  const p = panelFor(m.client);
  const line = document.createElement("div"); line.className = "tevent";
  const img = document.createElement("img");
  img.src = m.avatar || DEFAULT_AV; img.onerror = () => { img.style.visibility = "hidden"; };
  const txt = document.createElement("span"); txt.className = "etxt";
  const ts = (CFG && CFG.ui && CFG.ui.show_timestamps) ? '<span class="ts"></span> ' : "";
  txt.innerHTML = ts + "<b></b> " + escapeHtml(EVENT_LABEL[m.event] || m.event);
  txt.querySelector("b").textContent = m.name || "someone";
  if (ts) txt.querySelector(".ts").textContent = fmtTs(m.ts || Date.now());
  line.appendChild(img); line.appendChild(txt);
  placeRow(p, line);
  capLines(p); pinScroll(p);
}

function fmtTs(ts) {
  const fmt = (CFG && CFG.ui && CFG.ui.timestamp_format) || "clock";
  if (fmt === "relative") {
    const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    return s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago";
  }
  return new Date(ts).toLocaleTimeString([], { hour12: false });
}

function renderTranscript(m) {
  const p = panelFor(m.client);
  let line = p.cur[m.userId];
  if (!line) {
    line = document.createElement("div"); line.className = "tline";
    line.dataset.uid = m.userId;            // so retroactive renames can find this line
    const img = document.createElement("img");
    img.src = m.avatar || DEFAULT_AV;
    img.onerror = () => { img.style.visibility = "hidden"; };
    const body = document.createElement("div"); body.className = "body";
    body.innerHTML = `<div class="who"><span class="ts"></span><span class="nm"></span></div><div class="txt"></div>`;
    const nmEl = body.querySelector(".nm");
    nmEl.textContent = m.name; nmEl.style.color = colorFor(m.userId);   // per-user color
    line.appendChild(img); line.appendChild(body);
    placeRow(p, line);                                // newest at the configured end
    p.cur[m.userId] = line;
  }
  const showTs = CFG && CFG.ui && CFG.ui.show_timestamps;
  const tsEl = line.querySelector(".ts");
  tsEl.style.display = showTs ? "" : "none";
  if (showTs) tsEl.textContent = fmtTs(m.ts || Date.now());
  line.classList.toggle("interim", !m.isFinal);
  line.querySelector(".txt").textContent = m.text || (m.isFinal ? "" : "…");
  capLines(p); pinScroll(p);
  if (m.isFinal) {
    delete p.cur[m.userId];
    if (!m.text) { line.remove(); } else { p.n++; p.cnt.textContent = p.n; }
  }
}

function applyRename(m) {
  const p = panels[(m.client || "unknown").toLowerCase()];
  if (!p) return;
  const esc = (window.CSS && CSS.escape) ? CSS.escape(m.userId) : m.userId.replace(/"/g, '\\"');
  p.body.querySelectorAll('[data-uid="' + esc + '"]').forEach((line) => {
    const nm = line.querySelector(".nm"); if (nm) nm.textContent = m.name;
    const img = line.querySelector("img");
    if (img && m.avatar) { img.src = m.avatar; img.style.visibility = ""; }
  });
}

function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

// ---------- boot ----------
async function boot() {
  API = window.pywebview.api;
  initPills();
  initAutosave();
  CFG = await API.get_config();
  await populateDevices();
  fillForm(CFG);
  await refreshClients();
  await refreshEngine();
  refreshGpu();
  connectRelay();
  pumpLog();
  pumpProgress();
  setInterval(refreshEngine, 3000);
}

async function populateDevices() {
  try {
    const devs = await API.list_input_devices();
    const sel = $("self_device");
    devs.forEach((d) => {
      const o = document.createElement("option");
      o.value = String(d.index); o.textContent = d.name;
      sel.appendChild(o);
    });
  } catch (e) {}
}

async function refreshGpu() {
  try {
    const ok = await API.cuda_status();
    const el = $("gpuhint");
    if ($("adv_device").value !== "cuda") { el.textContent = ""; return; }
    el.textContent = ok ? "GPU runtime: ready ✓"
                        : "GPU runtime: will download (~1 GB) on first Start.";
    el.style.color = ok ? "var(--good)" : "var(--warn)";
  } catch (e) {}
}
window.addEventListener("pywebviewready", boot);
