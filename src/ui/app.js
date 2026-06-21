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
// stable per-source emoji so two undetected speakers are tell-apart-able while you assign them
const UNK_EMOJI = ["🦊","🐢","🦉","🦋","🐙","🦔","🦫","🐝","🦎","🐳","🦜","🐊","🦒","🦓","🦩","🦦","🐺","🦡","🐿️","🦃","🦚","🐌","🐠","🦂"];
function emojiFor(src) {
  let h = 0; const s = String(src || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return UNK_EMOJI[h % UNK_EMOJI.length];
}
// undetected speakers: the emoji becomes their AVATAR, so the name stays a clean "Unknown 1a2b3"
function unknownLabel(src) { return "Unknown " + String(src).slice(-5); }
function emojiAvatar(src) {   // render the per-source emoji as a round avatar (data-URI SVG)
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
    + '<rect width="40" height="40" rx="20" fill="#3a3c43"/>'
    + '<text x="50%" y="52%" dominant-baseline="central" text-anchor="middle" font-size="22">' + emojiFor(src) + '</text></svg>';
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}
const DEFAULT_AV_GRAY = "https://cdn.discordapp.com/embed/avatars/1.png";   // Discord's gray default avatar
const badAvatars = new Set();   // real avatar URLs that failed to load -> fall back to the emoji avatar
const rosters = {};   // client -> [{userId,name,avatar,stream,mute,deaf,video}]  (the call's members)
const speakingNow = {}; // client -> Set(userId) currently speaking (persists until the engine changes it)
const EMPTY_SET = new Set();
const sources = {};   // src -> {client,name,avatar,resolved,locked,kind,ts}  (live speakers seen)

// ---------- inline Lucide icons (offline; 24x24 stroke) ----------
const LU = {
  "log-in": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" x2="3" y1="12" y2="12"/>',
  "log-out": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
  "mic": '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
  "mic-off": '<line x1="2" x2="22" y1="2" y2="22"/><path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2"/><path d="M5 10v2a7 7 0 0 0 12 5"/><path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12"/><line x1="12" x2="12" y1="19" y2="22"/>',
  "volume-2": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  "volume-x": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" x2="16" y1="9" y2="15"/><line x1="16" x2="22" y1="9" y2="15"/>',
  "video": '<path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/>',
  "video-off": '<path d="M10.66 6H14a2 2 0 0 1 2 2v2.34l1 1L22 8v8"/><path d="M16 16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2l10 10Z"/><line x1="2" x2="22" y1="2" y2="22"/>',
  "screen-share": '<path d="M13 3H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-3"/><path d="M8 21h8"/><path d="M12 17v4"/><path d="m17 8 5-5"/><path d="M17 3h5v5"/>',
  "screen-share-off": '<path d="M13 3H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h7"/><path d="M8 21h8"/><path d="M12 17v4"/><path d="m22 3-5 5"/><path d="m17 3 5 5"/>',
  "trash-2": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
  "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  "rotate-cw": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  "arrow-up-down": '<path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/>',
  "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  "lock-open": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  "user-plus": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" x2="19" y1="8" y2="14"/><line x1="22" x2="16" y1="11" y2="11"/>',
};
function icon(name, cls) {
  return '<svg class="lu ' + (cls || "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (LU[name] || "") + "</svg>";
}
const EVENT_ICON = {
  joined: ["log-in", "#23a55a"], left: ["log-out", "#f23f43"],
  muted: ["mic-off", "#949ba4"], unmuted: ["mic", "#23a55a"],
  deafened: ["volume-x", "#949ba4"], undeafened: ["volume-2", "#23a55a"],
  video_on: ["video", "#5865f2"], video_off: ["video-off", "#949ba4"],
  stream_on: ["screen-share", "#5865f2"], stream_off: ["screen-share-off", "#949ba4"],
};

// ---------- keyword highlighting (whole-word, so "elly" doesn't fire on "belly") ----------
const _kwReCache = {};
function kwRe(k) {   // case-insensitive, global, not flanked by word characters
  return _kwReCache[k] || (_kwReCache[k] =
    new RegExp("(?<!\\w)" + k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?!\\w)", "gi"));
}
function matchKeyword(t) {
  if (!t || !kwList.length) return null;
  for (const raw of kwList) {
    const k = String(raw).toLowerCase(); if (!k) continue;
    const re = kwRe(k); re.lastIndex = 0;
    if (re.test(t)) return k;
  }
  return null;
}
function setHl(el, text, kw) {            // render text into el, wrapping whole-word kw matches in <mark>
  el.textContent = "";
  const t = text || "";
  if (!kw) { el.textContent = t; return; }
  const re = kwRe(kw); re.lastIndex = 0;
  let i = 0, mch;
  while ((mch = re.exec(t)) !== null) {
    if (mch.index > i) el.appendChild(document.createTextNode(t.slice(i, mch.index)));
    const m = document.createElement("mark"); m.textContent = mch[0]; el.appendChild(m);
    i = mch.index + mch[0].length;
    if (mch[0].length === 0) re.lastIndex++;
  }
  if (i < t.length) el.appendChild(document.createTextNode(t.slice(i)));
}
// re-run highlighting over every transcript line after a keyword edit
function rehighlightAll() {
  document.querySelectorAll("#transcript .tline .txt").forEach((tx) => {
    const text = tx.dataset.text || ""; setHl(tx, text, matchKeyword(text));
  });
  // push the live edit to the in-Discord overlays through the relay control bus
  try { if (relay && relay.readyState === 1) relay.send(JSON.stringify({ type: "setKeywords", keywords: kwList })); } catch (e) {}
}

// ---------- tiny markdown -> HTML (for the (i) help popovers) ----------
function mdToHtml(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, (m, c) => "<code>" + c + "</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const out = []; let list = null;
  for (const raw of String(md).split("\n")) {
    const line = raw.trim();
    if (/^[-*]\s+/.test(line)) { if (!list) { list = []; } list.push("<li>" + inline(line.replace(/^[-*]\s+/, "")) + "</li>"); continue; }
    if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
    if (line) out.push("<p>" + inline(line) + "</p>");
  }
  if (list) out.push("<ul>" + list.join("") + "</ul>");
  return out.join("");
}

// ---------- transcript direction (newest at bottom by default, flippable) ----------
const newestTop = () => !!(CFG && CFG.ui && CFG.ui.newest_at_top);   // global default for new panels
const jumpLabel = (top) => "Jump to latest " + (top ? "↑" : "↓");
const flipTitle = (top) => top ? "Newest on top - click for oldest first" : "Oldest on top - click for newest first";
function placeRow(p, el) {                 // insert at this panel's "newest" end
  if (p.newestTop) p.body.insertBefore(el, p.body.firstChild);
  else p.body.appendChild(el);
}
function flipPanel(p) {                     // per-card direction toggle
  p.newestTop = !p.newestTop;
  Array.from(p.body.children).reverse().forEach((r) => p.body.appendChild(r));
  if (p.jump) p.jump.textContent = jumpLabel(p.newestTop);
  if (p.flipBtn) p.flipBtn.title = flipTitle(p.newestTop);
  p.pinned = true; p.lastAuto = Date.now();
  p.body.scrollTop = p.newestTop ? 0 : p.body.scrollHeight;
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
  API.save_config(CFG); pushLiveConfig(CFG); markOverlayNeeded();
  toast("Overlay " + (on ? "on" : "off") + " for " + exe, false);
}
function selfFor(exe) {
  const cl = ((CFG && CFG.self_transcribe) || {}).clients || {};
  return cl[exe] !== false;
}
function setSelf(exe, on) {
  const s = Object.assign({}, CFG.self_transcribe);
  s.clients = Object.assign({}, s.clients || {}); s.clients[exe] = on; CFG.self_transcribe = s;
  API.save_config(CFG); pushLiveConfig(CFG); toast("Own-voice " + (on ? "on" : "off") + " for " + exe, false);
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
function activateTab(v) {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.v === v));
  document.querySelectorAll(".view").forEach((x) => x.classList.remove("active"));
  const view = $("v-" + v); if (view) view.classList.add("active");
  if (v === "live") { const tt = document.querySelector('.tab[data-v="live"]'); if (tt) tt.classList.remove("highlight"); }
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => activateTab(t.dataset.v)));

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
  $("cap_screen").checked = (c.capture || {}).screenshare !== false;
  const g = c.gating || {};
  $("g_dbfs").value = g.min_rms_dbfs ?? -50;
  $("g_dbfs_v").textContent = $("g_dbfs").value;
  $("g_vad").checked = g.vad !== false;
  $("g_reqspeak").checked = g.require_speaking !== false;
  $("g_drop").value = (g.drop_phrases || []).join(", ");
  const a = c.alerts || {};
  kwList = (a.keywords || []).slice(); renderPills();
  $("a_sound").checked = a.sound !== false;
  $("a_highlight").value = toHex(a.highlight);
  document.documentElement.style.setProperty("--alert", toHex(a.highlight));   // transcript <mark> color
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
  $("adv_device").value = c.device || "auto";
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
    capture: Object.assign({}, CFG.capture, { screenshare: $("cap_screen").checked }),
    language: $("adv_lang").value.trim(),
    beam_size: parseInt($("adv_beam").value, 10) || 1,
    device: $("adv_device").value,
    compute_type: $("adv_compute").value,
    relay_port: parseInt($("adv_relay").value, 10) || 8765,
    gating: Object.assign({}, CFG.gating, {
      min_rms_dbfs: parseFloat($("g_dbfs").value),
      vad: $("g_vad").checked,
      require_speaking: $("g_reqspeak").checked,
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
    tag.querySelector(".pill-x").onclick = () => { kwList.splice(i, 1); renderPills(); scheduleSave(); rehighlightAll(); };
    box.insertBefore(tag, input);
  });
}
function addKw(v) {
  v = v.trim().replace(/,$/, "").trim();
  if (v && !kwList.some((k) => k.toLowerCase() === v.toLowerCase())) { kwList.push(v); renderPills(); scheduleSave(); rehighlightAll(); }
}
function initPills() {
  const input = $("kw-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addKw(input.value); input.value = ""; }
    else if (e.key === "Backspace" && !input.value && kwList.length) { kwList.pop(); renderPills(); scheduleSave(); rehighlightAll(); }
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
// push the restart-free settings to the running engine over the relay control bus, so toggles
// like stream audio / own-voice / gating apply live without an engine restart
function pushLiveConfig(cfg) {
  try { if (relay && relay.readyState === 1) relay.send(JSON.stringify({ type: "setConfig", config: cfg })); } catch (e) {}
}
function scheduleSave() {
  if (!API) return;
  toast("Saving…", true);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { const cfg = readForm(); await API.save_config(cfg); CFG = cfg; pushLiveConfig(cfg); toast("Saved ✓", false); }
    catch (e) { toast("Save failed", false); }
  }, 450);
}
function initAutosave() {
  const v = $("v-settings");
  const onChange = (e) => {
    if (e.target.id === "kw-input") return;
    if (e.target.id === "adv_device") refreshGpu();
    if (e.target.id === "whisper_model") refreshModels();
    scheduleSave();
    if (LIVE_FIELDS.has(e.target.id)) return;                 // applies live, no prompt
    if (OVERLAY_FIELDS.has(e.target.id)) markOverlayNeeded();  // overlay-only -> re-inject, not engine restart
    else markRestartNeeded();                                 // engine setting changed -> prompt restart
  };
  v.addEventListener("input", onChange);
  v.addEventListener("change", onChange);
}
// settings that apply live (UI-side, or pushed to the engine over the relay) and never restart.
// Engine-side live fields are mirrored by apply_live_config() in live_transcribe.py.
const LIVE_FIELDS = new Set([
  "ui_newtop", "ui_ts", "ui_tsfmt", "a_highlight",        // wrapper-only display prefs
  "ui_events",                                            // show voice events (engine emits live)
  "cap_screen",                                            // transcribe stream audio
  "self_en", "self_unmute", "self_vad", "self_device",     // own-voice (incl. live device switch)
  "g_dbfs", "g_vad", "g_reqspeak", "g_drop",               // silence gating
  "adv_lang", "adv_beam",                                  // language + beam size
]);
// overlay-only settings: applied by re-injecting the overlay into Discord, NOT by an engine restart
const OVERLAY_FIELDS = new Set(["o_timeout", "o_max", "o_fade", "o_minop", "o_logh", "a_sound"]);
function markRestartNeeded() { if (engineRunning) $("restartbar").style.display = "flex"; }
function clearRestartNeeded() { $("restartbar").style.display = "none"; }
function markOverlayNeeded() { if (engineRunning) $("overlaybar").style.display = "flex"; }
function clearOverlayNeeded() { $("overlaybar").style.display = "none"; }

// Re-inject the overlay into Discord with the current settings, without stopping the engine.
async function reinjectOverlay(btn) {
  if (btn) btn.disabled = true;
  clearOverlayNeeded();
  toast("Restarting overlay…", true);
  try { pushLiveConfig(readForm()); } catch (e) {}     // make sure the engine has the latest overlay config
  try { if (relay && relay.readyState === 1) relay.send(JSON.stringify({ type: "reinjectOverlay" })); } catch (e) {}
  setTimeout(() => { toast("Overlay restarted ✓", false); if (btn) btn.disabled = false; }, 700);
}
$("overlaybar-btn").addEventListener("click", () => reinjectOverlay($("overlaybar-btn")));

async function restartEngine(btn) {
  if (btn) btn.disabled = true;
  toast("Restarting engine…", true);
  clearRestartNeeded(); clearOverlayNeeded();   // a full restart re-injects the overlay too
  try { await API.stop_backend(); await API.start_backend(); } catch (e) {}
  showProgress({ active: true, done: false, pct: null, label: "Restarting engine…" });
  setTimeout(() => { refreshEngine(); connectRelay(); refreshModels(); toast("Engine restarted ✓", false); if (btn) btn.disabled = false; }, 1300);
}
$("restartbtn").addEventListener("click", () => restartEngine($("restartbtn")));
$("restartbar-btn").addEventListener("click", () => restartEngine($("restartbar-btn")));
// live highlight color: update the transcript <mark> color without a restart
$("a_highlight").addEventListener("input", () => document.documentElement.style.setProperty("--alert", $("a_highlight").value));

// ---------- update check ----------
// Checks GitHub for a newer release on every launch. "Ignore" only hides the bar for this
// session (no persistence), so the next relaunch re-checks and re-prompts if still behind.
async function checkUpdate() {
  if (!API) return;
  let u;
  try { u = await API.check_update(); } catch (e) { return; }
  if (!u || !u.available) return;
  $("updatebar-text").textContent = `Update available: v${u.latest} (you have v${u.current}).`;
  $("updatebar").dataset.url = u.url || "";
  $("updatebar").style.display = "flex";
}
$("updatebar-btn").addEventListener("click", () => {
  const url = $("updatebar").dataset.url;
  if (url) API.open_url(url);
});
$("updatebar-ignore").addEventListener("click", () => { $("updatebar").style.display = "none"; });

// ---------- clients ----------
let clientList = [];
let engineStatus = {};        // exe -> {hooked, cdp, streams, active, mapped} from the engine heartbeat
let engineRunning = false;    // last known engine state (drives the "restart to apply" bar)
const dismissedReminders = new Set();   // reminder keys the user closed this session

async function refreshClients() { clientList = await API.list_clients(); renderClients(); }

// Dismissable nudges explaining why names may show as "user 1a2b3" - a client without a connected
// debug port can't resolve names. Only shown while the engine is running (i.e. actually capturing).
function renderReminders() {
  const box = $("reminders"); if (!box) return;
  const active = [];
  if (engineRunning) {
    for (const c of clientList) {
      const es = engineStatus[c.exe];
      const capturingNoNames = es && es.hooked && !es.cdp;   // capturing this client but no CDP -> no names
      const runningNoPort = c.running && !c.live;            // running without a debug port at all
      const onScreenNeeded = es && es.hooked && es.cdp && es.active > 0 && !es.mapped;   // connected but nothing resolves
      if (capturingNoNames || runningNoPort) {
        active.push({
          key: "noport:" + c.folder, folder: c.folder, fix: true,
          text: clientLabel(c.exe) + " has no debug port connected, so its speakers show as “Unknown 1a2b3”. "
              + "Restart it with its port to resolve names, or assign them by hand in the Speakers tab "
              + "or by clicking a name in the Transcript.",
        });
      } else if (onScreenNeeded) {
        active.push({
          key: "onscreen:" + c.folder, folder: c.folder, fix: false,
          text: clientLabel(c.exe) + " is connected but isn't resolving any names. Keep its voice call "
              + "visible on screen - name lookup reads the on-screen voice panel.",
        });
      }
    }
  }
  // forget dismissals once their condition clears, so a later recurrence shows again
  const activeKeys = new Set(active.map((a) => a.key));
  for (const k of [...dismissedReminders]) if (!activeKeys.has(k)) dismissedReminders.delete(k);

  box.innerHTML = "";
  for (const r of active) {
    if (dismissedReminders.has(r.key)) continue;
    const row = document.createElement("div"); row.className = "reminder";
    const tx = document.createElement("span"); tx.textContent = r.text;
    const grow = document.createElement("span"); grow.className = "grow";
    row.append(tx, grow);
    if (r.fix) {
      const fix = document.createElement("button"); fix.className = "sec"; fix.textContent = "Restart w/ port";
      fix.onclick = async () => { fix.disabled = true; fix.textContent = "…"; try { await API.ensure_client(r.folder, true); } catch (e) {} setTimeout(refreshClients, 1500); };
      row.append(fix);
    }
    const x = document.createElement("span"); x.className = "rx"; x.textContent = "×"; x.title = "Dismiss";
    x.onclick = () => { dismissedReminders.add(r.key); renderReminders(); };
    row.append(x);
    box.appendChild(row);
  }
}

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
      tip = cdp ? `Hooked + names resolving via CDP on port ${c.port} (${mapped} mapped).`
                : "Audio hooked, but NO debug port - names stay as “user …”. Use Restart w/ port.";
    } else if (c.live) {
      dot = "info"; label = `debug port ${c.port} ready`;
      tip = `Debug port ${c.port} open. Will attach once you Start the engine and a call is active.`;
    } else if (c.running) {
      dot = "warn"; label = "running - no debug port";
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
    if (c.live) {                                     // debug port already connected -> nothing to do
      btn.textContent = "Ready"; btn.disabled = true;
    } else {
      btn.textContent = c.running ? "Restart w/ port" : "Launch";
      btn.onclick = async () => {
        btn.disabled = true; btn.textContent = "…";
        await API.ensure_client(c.folder, c.running && !c.live);
        setTimeout(refreshClients, 1500);
      };
    }
    row.appendChild(btn);
    box.appendChild(row);
  }
  renderToggleList("overlay_clients", injectFor, setInject);
  renderToggleList("self_clients", selfFor, setSelf);
  renderReminders();
}

// ---------- engine start/stop ----------
async function refreshEngine() {
  const running = await API.backend_status();
  engineRunning = running;
  $("bdot").className = "dot " + (running ? "on" : "off");
  $("bstat").textContent = running ? "engine running" : "stopped";
  $("startbtn").disabled = running;
  $("stopbtn").disabled = !running;
  if (!running) { clearRestartNeeded(); clearOverlayNeeded(); }   // nothing to apply while stopped
  renderReminders();                          // reflect engine state in the name-resolution nudges
}
$("startbtn").addEventListener("click", async () => {
  $("startbtn").disabled = true;
  // nudge toward the Transcript tab when starting from elsewhere; the highlight clears once you open it
  const tt = document.querySelector('.tab[data-v="live"]');
  if (tt && !tt.classList.contains("active")) tt.classList.add("highlight");
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
    if (relay !== sock) return;                 // superseded by a newer socket - don't reconnect
    $("rdot").className = "dot off"; $("rstat").textContent = "relay off";
    setTimeout(connectRelay, 2000);
  };
  sock.onmessage = (ev) => {
    if (relay !== sock) return;
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "status") {
      $("activepill").textContent = (m.active || 0) + " stream" + (m.active === 1 ? "" : "s");
      if (m.clients) { engineStatus = m.clients; renderClients(); }
    } else if (m.type === "transcript") {
      trackSource(m);
      renderTranscript(m);
    } else if (m.type === "event") {
      renderEvent(m);
    } else if (m.type === "rename") {
      trackSource(m);
      applyRename(m);
    } else if (m.type === "roster") {
      rosters[m.client] = m.members || [];
      renderColumnRoster(panelFor(m.client));  // ensure the column exists so the faces can show
    } else if (m.type === "speaking") {
      speakingNow[m.client] = new Set(m.ids || []);
      const p = panels[(m.client || "").toLowerCase()];
      if (p) renderColumnRoster(p);
    }
  };
}

// remember each live source's current identity so the Speakers list + pickers can show/fix it
function trackSource(m) {
  const s = sources[m.userId] || (sources[m.userId] = {});
  s.client = m.client; s.ts = Date.now();
  if (m.name !== undefined) s.name = m.name;
  if (m.avatar !== undefined) s.avatar = m.avatar;
  if (m.kind !== undefined) s.kind = m.kind;
  if (m.resolved !== undefined) s.resolved = m.resolved;
  if (m.locked !== undefined) s.locked = m.locked;
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
  const rosterHead = document.createElement("span"); rosterHead.className = "tcol-roster";   // faces inline
  const cnt = document.createElement("span"); cnt.className = "cnt"; cnt.textContent = "0";
  const flip = document.createElement("span"); flip.className = "tcol-flip"; flip.innerHTML = icon("arrow-up-down");
  const clr = document.createElement("span"); clr.className = "tcol-clear"; clr.textContent = "clear";
  clr.title = "Clear this client's transcript";
  head.appendChild(dot); head.appendChild(title); head.appendChild(rosterHead); head.appendChild(cnt); head.appendChild(flip); head.appendChild(clr);

  const body = document.createElement("div"); body.className = "tcol-body";
  const jump = document.createElement("button"); jump.className = "jump"; jump.textContent = jumpLabel(newestTop());
  const rosterBottom = document.createElement("div"); rosterBottom.className = "tcol-roster bottom"; rosterBottom.style.display = "none";

  col.appendChild(head); col.appendChild(body); col.appendChild(rosterBottom); col.appendChild(jump);
  // keep columns ordered by label for stable layout
  const cols = Array.from(box.children);
  const after = cols.find((c) => c._label && c._label > clientLabel(client));
  if (after) box.insertBefore(col, after); else box.appendChild(col);
  col._label = clientLabel(client);

  // "pinned" = scrolled to the newest end (top when newestTop, else bottom). Direction is per-panel,
  // seeded from the global default and flippable on the card itself.
  const p = { col, body, jump, cnt, client: key, rosterHead, rosterBottom, flipBtn: flip,
              newestTop: newestTop(), pinned: true, n: 0, cur: {}, lastAuto: 0, jt: null };
  // recompute the face row when the column is resized (responsive header-vs-bottom + fit)
  try { p.ro = new ResizeObserver(() => renderColumnRoster(p)); p.ro.observe(col); } catch (e) {}
  flip.title = flipTitle(p.newestTop);
  flip.onclick = () => flipPanel(p);
  clr.onclick = () => { body.innerHTML = ""; p.cur = {}; p.n = 0; cnt.textContent = "0"; };
  body.addEventListener("scroll", () => {
    if (Date.now() - p.lastAuto < 130) return;            // ignore our own auto-scroll -> no flicker
    const atEnd = p.newestTop ? (body.scrollTop < 28)
                              : (body.scrollTop + body.clientHeight >= body.scrollHeight - 28);
    if (atEnd) { p.pinned = true; jump.style.display = "none"; if (p.jt) { clearTimeout(p.jt); p.jt = null; } }
    else { p.pinned = false; if (!p.jt) p.jt = setTimeout(() => { if (!p.pinned) jump.style.display = "block"; p.jt = null; }, 180); }
  });
  jump.addEventListener("click", () => {
    p.pinned = true; jump.style.display = "none"; p.lastAuto = Date.now();
    body.scrollTop = p.newestTop ? 0 : body.scrollHeight;
  });
  panels[key] = p;
  return p;
}

function scrollToEnd(p) { p.lastAuto = Date.now(); p.body.scrollTop = p.newestTop ? 0 : p.body.scrollHeight; }
function pinScroll(p) {
  if (!p.pinned) return;
  scrollToEnd(p);
  // Re-apply on the next frame: a just-appended row (a voice event, or wrapped text) can lay out
  // taller than at first measure, which otherwise leaves us a few px short and "stuck".
  requestAnimationFrame(() => { if (p.pinned) scrollToEnd(p); });
}
function capLines(p) {                       // drop the OLDEST rows (opposite end from newest)
  while (p.body.children.length > 200) p.body.removeChild(p.newestTop ? p.body.lastChild : p.body.firstChild);
}
// ---- per-column roster faces (green ring on whoever's speaking, replaces "N speaking") ----
const RFACE_SLOT = 26;   // avatar (22px) + gap (4px)
const RFACE_W = 22, RGAP = 4, RSTREAM_W = 16, RMORE_W = 30;   // px used in the width/fit math
const memberW = (m) => RFACE_W + RGAP + (m.stream ? RSTREAM_W + RGAP : 0);   // a streaming member is wider
function rosterFace(m, speaking) {
  const f = document.createElement("span");
  f.className = "rface" + (speaking.has(m.userId) ? " speaking" : "");
  f.title = m.name || "user";
  const img = document.createElement("img");
  img.src = m.avatar || DEFAULT_AV_GRAY;
  img.onerror = () => { img.src = DEFAULT_AV_GRAY; };
  f.appendChild(img);
  // state badges in the avatar's corner (no extra width): deaf > mute, plus video
  if (m.deaf) f.appendChild(badge("volume-x", "bad"));
  else if (m.mute) f.appendChild(badge("mic-off", "mut"));
  if (m.video) f.appendChild(badge("video", "vid"));
  return f;
}
function badge(name, cls) { const b = document.createElement("span"); b.className = "rbadge " + cls; b.innerHTML = icon(name); return b; }
function paintFaces(host, list, speaking, moreCount) {
  host.innerHTML = "";
  for (const m of list) {
    host.appendChild(rosterFace(m, speaking));
    if (m.stream) {                              // stream icon sits next to the face (counts toward width)
      const s = document.createElement("span"); s.className = "rstream"; s.title = (m.name || "user") + " is streaming";
      s.innerHTML = icon("screen-share"); host.appendChild(s);
    }
  }
  if (moreCount > 0) {
    const more = document.createElement("span"); more.className = "rmore"; more.textContent = "+" + moreCount;
    more.title = moreCount + " more in the call"; host.appendChild(more);
  }
}
function fitCount(width, list, reserve) {        // greedily fit by actual member widths
  let used = 0, n = 0;
  for (const m of list) { used += memberW(m); if (used > width - reserve) break; n++; }
  return Math.max(1, n);
}
const totalW = (list) => list.reduce((s, m) => s + memberW(m), 0);
// the call's members as faces: inline in the header while they fit, else a full-width strip at the
// bottom; speakers stay visible (a hidden speaker bumps a quiet one) with the rest collapsed to "+N".
function renderColumnRoster(p) {
  if (!p) return;
  const members = (rosters[p.client] || []).slice().sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  const speaking = speakingNow[p.client] || EMPTY_SET;
  let host, list, more = 0;
  if (!members.length) {
    host = p.rosterHead; list = [];
  } else if (totalW(members) <= (p.rosterHead.clientWidth || 0)) {
    host = p.rosterHead; list = members;          // all fit inline in the header
  } else {
    host = p.rosterBottom;                         // too long -> full-width strip at the bottom
    const botW = p.rosterBottom.clientWidth || (p.col.clientWidth - 20);
    if (totalW(members) <= botW) { list = members; }
    else {                                         // still overflowing: speakers first, then "+N"
      const ordered = members.filter((m) => speaking.has(m.userId)).concat(members.filter((m) => !speaking.has(m.userId)));
      const n = fitCount(botW, ordered, RMORE_W);
      list = ordered.slice(0, n); more = members.length - n;
    }
  }
  // skip the rebuild when nothing visible changed (avoids churn from the 250ms ticker)
  const sig = host.className + "|" + more + "|" + list.map((m) =>
    m.userId + (speaking.has(m.userId) ? "S" : "") + (m.mute ? "m" : "") + (m.deaf ? "d" : "") + (m.stream ? "x" : "") + (m.video ? "v" : "")).join(",");
  if (p._rsig === sig) return;
  p._rsig = sig;
  if (host === p.rosterHead) { p.rosterBottom.style.display = "none"; p.rosterBottom.innerHTML = ""; }
  else { p.rosterBottom.style.display = "flex"; p.rosterHead.innerHTML = ""; }
  paintFaces(host, list, speaking, more);
}
// the global "Newest on top" setting resets every panel to that direction (per-card flips override until then)
function applyDirection() {
  const top = newestTop();
  Object.values(panels).forEach((p) => {
    if (p.newestTop !== top) {
      p.newestTop = top;
      Array.from(p.body.children).reverse().forEach((r) => p.body.appendChild(r));
    }
    if (p.jump) p.jump.textContent = jumpLabel(p.newestTop);
    if (p.flipBtn) p.flipBtn.title = flipTitle(p.newestTop);
    p.pinned = true; p.lastAuto = Date.now();
    p.body.scrollTop = p.newestTop ? 0 : p.body.scrollHeight;
  });
}

function renderEvent(m) {
  if (CFG && CFG.voice_events === false) return;
  const p = panelFor(m.client);
  const line = document.createElement("div"); line.className = "tevent";
  const meta = EVENT_ICON[m.event];
  const ico = document.createElement("span");
  ico.innerHTML = icon(meta ? meta[0] : "info");
  if (meta) ico.style.color = meta[1];
  const txt = document.createElement("span"); txt.className = "etxt";
  const ts = (CFG && CFG.ui && CFG.ui.show_timestamps) ? '<span class="ts"></span> ' : "";
  txt.innerHTML = ts + "<b></b> " + escapeHtml(EVENT_LABEL[m.event] || m.event);
  txt.querySelector("b").textContent = m.name || "someone";
  if (ts) txt.querySelector(".ts").textContent = fmtTs(m.ts || Date.now());
  line.appendChild(ico);
  if (m.avatar) {                                    // show the user's avatar on the event row
    const av = document.createElement("img"); av.src = m.avatar; av.alt = "";
    av.onerror = () => { av.style.visibility = "hidden"; };
    av.onload = () => pinScroll(p);                  // keep the bottom pinned once the avatar lays out
    line.appendChild(av);
  }
  line.appendChild(txt);
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
    img.onerror = () => { img.style.visibility = "hidden"; };
    img.onload = () => pinScroll(p);                  // re-pin once the avatar lays out
    const body = document.createElement("div"); body.className = "body";
    body.innerHTML = `<div class="who"><span class="ts"></span><span class="nm"></span></div><div class="txt"></div>`;
    line.appendChild(img); line.appendChild(body);
    placeRow(p, line);                                // newest at the configured end
    p.cur[m.userId] = line;
  }
  applySpeaker(line, m.userId, m.client);             // avatar + name (or Unknown) + click-to-assign
  const showTs = CFG && CFG.ui && CFG.ui.show_timestamps;
  const tsEl = line.querySelector(".ts");
  tsEl.style.display = showTs ? "" : "none";
  if (showTs) tsEl.textContent = fmtTs(m.ts || Date.now());
  line.classList.toggle("interim", !m.isFinal);
  const txEl = line.querySelector(".txt");
  const text = m.text || (m.isFinal ? "" : "…");
  txEl.dataset.text = text;                          // raw text kept for retroactive re-highlight
  setHl(txEl, text, matchKeyword(text));
  capLines(p); pinScroll(p);
  if (m.isFinal) {
    delete p.cur[m.userId];
    if (!m.text) { line.remove(); } else { p.n++; p.cnt.textContent = p.n; }
  }
}

function applyRename(m) {
  const esc = (window.CSS && CSS.escape) ? CSS.escape(m.userId) : String(m.userId).replace(/"/g, '\\"');
  document.querySelectorAll('#transcript .tline[data-uid="' + esc + '"]')
    .forEach((line) => applySpeaker(line, m.userId, m.client));
}

// paint a transcript line's avatar + name from the source's current identity, and wire the
// name/avatar as a click target to (re)assign the speaker.
function applySpeaker(line, src, client) {
  const s = sources[src] || {};
  const img = line.querySelector("img");
  const nmEl = line.querySelector(".nm");
  if (!img || !nmEl) return;
  img.style.visibility = "";
  if (s.resolved && s.avatar && !badAvatars.has(s.avatar)) {
    img.src = s.avatar;
    img.onerror = () => { img.onerror = null; badAvatars.add(s.avatar); img.src = emojiAvatar(src); };  // real avatar failed -> emoji
  } else {
    img.src = emojiAvatar(src);                      // undetected, or a manual name with no avatar
    img.onerror = null;
  }
  nmEl.textContent = s.resolved ? (s.name || "") : unknownLabel(src);
  nmEl.style.color = s.resolved ? colorFor(src) : "var(--mut)";
  if (s.locked) nmEl.insertAdjacentHTML("beforeend", ' <span class="lk">' + icon("lock", "lk") + "</span>");
  nmEl.style.cursor = img.style.cursor = "pointer";
  nmEl.title = img.title = "Click to assign this speaker";
  const open = (e) => { e.stopPropagation(); openAssignPicker(src, s.client || client, nmEl); };
  nmEl.onclick = open; img.onclick = open;
}

// ---------- assign / reassign picker ----------
function sendAssign(src, payload) {
  try { if (relay && relay.readyState === 1) relay.send(JSON.stringify(Object.assign({ type: "assign", src: src }, payload))); } catch (e) {}
}
let assignPop = null;
function closeAssign() { if (assignPop) { assignPop.remove(); assignPop = null; } }
function positionPop(pop, anchor) {
  const r = anchor.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = r.left, top = r.bottom + 6;
  if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
  if (top + h > window.innerHeight - 8) top = r.top - h - 6;
  pop.style.left = Math.max(8, left) + "px"; pop.style.top = Math.max(8, top) + "px";
}
function openAssignPicker(src, client, anchor) {
  closeAssign(); closeHelp();
  const members = (rosters[client] || rosters[(client || "").toLowerCase()] || [])
    .slice().sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  const cur = sources[src] || {};
  const pop = document.createElement("div"); pop.className = "assign-pop";
  let html = '<div class="ap-h">Assign speaker</div>';
  if (members.length) {
    html += '<div class="ap-list">' + members.map((u) =>
      `<div class="ap-item" data-uid="${u.userId}"><img src="${u.avatar || DEFAULT_AV_GRAY}">` +
      `<span>${escapeHtml(u.name || "user")}</span>${u.stream ? '<small>stream</small>' : ''}</div>`).join("") + '</div>';
  } else {
    html += '<div class="ap-empty">No call roster - this client has no debug port, so names can\'t be listed. '
          + 'Restart it with its port, or type a name / paste a user ID below.</div>';
  }
  html += '<div class="ap-manual"><input class="ap-input" type="text" placeholder="type a name… or paste a user ID" /></div>';
  if (cur.locked) html += '<div class="ap-clear">' + icon("lock-open") + ' Clear lock (back to auto-detect)</div>';
  pop.innerHTML = html;
  document.body.appendChild(pop);
  positionPop(pop, anchor);
  pop.addEventListener("click", (e) => e.stopPropagation());
  pop.querySelectorAll(".ap-item").forEach((it) =>
    it.onclick = () => { sendAssign(src, { userId: it.dataset.uid }); closeAssign(); });
  const input = pop.querySelector(".ap-input");
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const v = input.value.trim(); if (!v) return;
    if (/^\d{15,21}$/.test(v)) sendAssign(src, { userId: v }); else sendAssign(src, { name: v });
    closeAssign();
  });
  const clr = pop.querySelector(".ap-clear");
  if (clr) clr.onclick = () => { sendAssign(src, { clear: true }); closeAssign(); };
  setTimeout(() => input.focus(), 0);
  assignPop = pop;
}

// ---------- Speakers tab (grouped per Discord client) ----------
function speakerRow(src, s) {
  const row = document.createElement("div"); row.className = "spkrow";
  const img = document.createElement("img"); img.className = "spk-av";
  if (s.resolved && s.avatar && !badAvatars.has(s.avatar)) {
    img.src = s.avatar;
    img.onerror = () => { badAvatars.add(s.avatar); img.src = emojiAvatar(src); };
  } else { img.src = emojiAvatar(src); }
  const nm = document.createElement("span"); nm.className = "nm";
  nm.textContent = s.resolved ? (s.name || "") : unknownLabel(src);
  if (!s.resolved) nm.style.color = "var(--mut)";
  const meta = document.createElement("span"); meta.className = "sub";
  meta.innerHTML = (s.kind === "stream" ? "stream" : "voice") + (s.locked ? ' · ' + icon("lock", "lk") + " locked" : "");
  const grow = document.createElement("span"); grow.className = "grow";
  const btn = document.createElement("button"); btn.className = "sec"; btn.textContent = s.locked ? "Reassign" : "Assign";
  btn.onclick = (e) => { e.stopPropagation(); openAssignPicker(src, s.client, btn); };
  row.append(img, nm, meta, grow, btn);
  if (s.locked) {
    const clr = document.createElement("span"); clr.className = "spk-clear"; clr.innerHTML = icon("lock-open"); clr.title = "Clear lock (back to auto-detect)";
    clr.onclick = (e) => { e.stopPropagation(); sendAssign(src, { clear: true }); };
    row.append(clr);
  }
  return row;
}
function renderSpeakers() {
  const box = $("speakers"); if (!box) return;
  const now = Date.now();
  const live = Object.entries(sources).filter(([, s]) => now - (s.ts || 0) < 60000);
  if (!engineRunning || !live.length) {
    box.innerHTML = '<div class="empty">Active speakers appear here once the engine is running.</div>';
    return;
  }
  const byClient = {};   // each Discord client has its own call/speakers - scope the list per client
  for (const [src, s] of live) { (byClient[s.client || "unknown"] = byClient[s.client || "unknown"] || []).push([src, s]); }
  box.innerHTML = "";
  Object.keys(byClient).sort((a, b) => clientLabel(a).localeCompare(clientLabel(b))).forEach((cl) => {
    const head = document.createElement("div"); head.className = "spk-head";
    const dot = document.createElement("span"); dot.className = "cdot"; dot.style.background = CLIENT_COLORS[cl] || "#5865f2";
    const hn = document.createElement("span"); hn.textContent = clientLabel(cl);
    head.append(dot, hn); box.appendChild(head);
    byClient[cl].sort((a, b) => b[1].ts - a[1].ts).forEach(([src, s]) => box.appendChild(speakerRow(src, s)));
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
  refreshModels();
  attachHelp();
  $("restartbtn").innerHTML = icon("rotate-cw") + "Restart engine to apply";
  $("restartbar-btn").innerHTML = icon("rotate-cw") + "Restart engine";
  $("updatebar-btn").innerHTML = icon("download") + "Update";
  setInterval(refreshEngine, 3000);
  setInterval(renderSpeakers, 1500);
  document.addEventListener("click", closeAssign);
  window.addEventListener("resize", closeAssign);
  checkUpdate();
}

// ---------- downloaded models ----------
async function refreshModels() {
  let list = [];
  try { list = await API.list_models(); } catch (e) {}
  const box = $("models");
  const current = ($("whisper_model").value || "").toLowerCase();
  if (!list.length) {
    box.innerHTML = '<div class="hint">No models downloaded yet - the selected model downloads on first Start.</div>';
    return;
  }
  box.innerHTML = "";
  for (const m of list) {
    const row = document.createElement("div"); row.className = "modelrow";
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = m.name;
    row.appendChild(nm);
    if (m.name.toLowerCase() === current) {
      const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = "active"; row.appendChild(badge);
    }
    const sz = document.createElement("span"); sz.className = "sz"; sz.textContent = (m.size_mb >= 1024 ? (m.size_mb / 1024).toFixed(1) + " GB" : m.size_mb + " MB");
    row.appendChild(sz);
    const tr = document.createElement("span"); tr.className = "trash"; tr.innerHTML = icon("trash-2");
    tr.title = "Delete " + m.name;
    tr.onclick = async () => {
      if (m.name.toLowerCase() === current && engineRunning) { toast("Stop the engine before deleting the active model", false); return; }
      tr.style.pointerEvents = "none";
      try { await API.delete_model(m.name); toast("Deleted " + m.name, false); } catch (e) { toast("Delete failed", false); }
      refreshModels();
    };
    row.appendChild(tr);
    box.appendChild(row);
  }
}

// ---------- (i) help popovers with markdown ----------
const HELP = {
  whisper_model: "**Speech model.** Bigger = more accurate, slower, more VRAM.\n- `tiny`/`base` - fastest, rough\n- `small` - good balance (default)\n- `medium`/`large-v3` - best accuracy (needs a strong GPU)\n\nModels download once and are reused - switching back never re-downloads.",
  adv_lang: "**Language.** `Auto-detect` lets Whisper guess per utterance. Pin a language to stop it switching mid-call and to speed things up slightly.",
  cap_screen: "**Transcribe stream audio.** Include Go Live / screenshare audio (game, music, video) in transcription. Off = only people's microphones. Applies live, no restart.",
  self_en: "**Transcribe your own microphone** in addition to everyone else's audio. Uses your mic, gated by Discord's own mute/VAD state below.",
  self_unmute: "Only capture your mic while you are **unmuted in Discord**. Off = transcribe even when self-muted.",
  self_vad: "Only capture your mic when **Discord's voice activity** says you're speaking - avoids transcribing background room noise.",
  g_dbfs: "**Silence gate.** Audio quieter than this (in dBFS) is skipped before it ever reaches the model. Higher (e.g. -45) gates harder and kills phantom *\"Thank you.\"* on near-silence.",
  g_vad: "**Silero VAD** trims non-speech regions from each chunk before transcription - fewer hallucinations on noise.",
  g_reqspeak: "**End when not speaking.** Closes an utterance once Discord's per-user speaking indicator goes quiet (after a short grace), which stops screenshare/comfort-noise bleed from transcribing forever. If your speech is being split into too many lines, turn this off to segment purely by audio.",
  g_drop: "**Drop phrases** (comma-separated) that Whisper hallucinates on silence (e.g. `thank you, bye`). Dropped only when the audio is quiet or low-confidence.",
  kw: "**Keyword alerts.** Words that get **highlighted** + a beep when spoken (e.g. your name). Editing these re-highlights the existing transcript live.",
  a_sound: "Play a short **beep** when a keyword is detected.",
  a_highlight: "**Highlight color** used to mark keyword hits in the transcript and overlay.",
  ui_events: "Show **voice events** (join/leave, mute, deafen, camera, stream) in the transcript and overlay, with icons.",
  ui_newtop: "**Newest on top.** Off = newest lines at the bottom (classic chat). On = newest pops in at the top.",
  ui_ts: "Show a **timestamp** on each transcript line.",
  ui_tsfmt: "Timestamp style: **clock** (`14:03:22`) or **relative** (`12s ago`).",
  o_logh: "Height of the in-Discord transcript log panel, in pixels (also drag-resizable).",
  o_timeout: "How long a subtitle stays on screen **after speech stops**, in milliseconds.",
  o_max: "Maximum number of subtitle blocks shown on the overlay at once.",
  o_fade: "Start fading older subtitles once this many are stacked.",
  o_minop: "Lowest opacity a faded subtitle reaches (0–1).",
  adv_beam: "**Beam size.** `1` = greedy & fastest. Higher = more accurate but slower.",
  adv_device: "**cuda** runs on your NVIDIA GPU (fast). **cpu** works anywhere but is much slower.",
  adv_compute: "Numeric precision. `float16` is best on GPU; `int8`/`int8_float16` use less memory; `float32` is CPU-friendly.",
  adv_relay: "Local WebSocket port the overlay connects to. Change only if `8765` clashes with something.",
};
let helpPop = null;
function closeHelp() { if (helpPop) { helpPop.remove(); helpPop = null; } }
function openHelp(anchor, md) {
  closeHelp();
  const pop = document.createElement("div"); pop.className = "help-pop"; pop.innerHTML = mdToHtml(md);
  pop.addEventListener("click", (e) => e.stopPropagation());
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = r.left, top = r.bottom + 6;
  if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
  if (top + h > window.innerHeight - 8) top = r.top - h - 6;
  pop.style.left = Math.max(8, left) + "px"; pop.style.top = Math.max(8, top) + "px";
  helpPop = pop;
}
function attachHelp() {
  for (const id in HELP) {
    const el = $(id); if (!el) continue;
    const row = el.closest(".row") || el.closest(".clientrow") || el.parentElement;
    const label = row && row.querySelector("label");
    const target = label || (el.closest(".row") ? null : el.previousElementSibling);
    if (!target || target.querySelector(".help-ic")) continue;
    const ic = document.createElement("span"); ic.className = "help-ic"; ic.innerHTML = icon("info");
    ic.tabIndex = 0; ic.title = "";
    const md = HELP[id];
    ic.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); if (helpPop && helpPop._for === ic) { closeHelp(); } else { openHelp(ic, md); helpPop._for = ic; } });
    target.appendChild(ic);
  }
  document.addEventListener("click", closeHelp);
  window.addEventListener("resize", closeHelp);
  document.querySelectorAll(".view").forEach((v) => v.addEventListener("scroll", closeHelp, true));
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
