// @ts-nocheck
import { OVERLAY_EVENT } from "../../shared/events";
import { svgIcon } from "../../shared/icons";
import { keywordRegex } from "../../shared/keywords";
import { SOUND_EVENT_RE } from "../../shared/sounds";
import { colorFor, markerAvatar as emojiAvatar, speakerDisplay } from "../../shared/speakers";

// BetterDiscord-free overlay injected into Discord's renderer via CDP.
// Subtitles (activity-based scale + live cursor + keyword alerts) + scrollable transcript log + status pills.
// Settings come from window.__VT_CONFIG (set by the orchestrator before injection).
(() => {
  if (window.top !== window.self) return "subframe";
  try { if (window.__vtOverlay && window.__vtOverlay.destroy) window.__vtOverlay.destroy(); } catch (e) {}

  const C = window.__VT_CONFIG || {};
  const OV = C.overlay || {}, AL = C.alerts || {};
  const CLIENT = C.client || null;            // this overlay only shows its own client's audio
  const RELAY = "ws://127.0.0.1:" + (C.relay_port || 8765);
  const LIVE_MS = OV.subtitle_timeout_ms || 8000;
  const MAX_BLOCKS = OV.max_blocks || 6;
  const FADE_START = OV.fade_start_count || 5;
  const MIN_FADE = OV.min_fade_opacity != null ? OV.min_fade_opacity : 0.25;
  const SHRINK_QUIET = OV.shrink_quiet_subtitles === true;
  const MERGE_SUBS = OV.merge_subtitles !== false;   // join a user's consecutive utterances in one bubble
  const SHOW_SUBS = OV.show_subtitles !== false;     // bottom subtitle bubbles
  const SHOW_LOG = OV.show_log !== false;            // top-right transcript log panel
  const SHOW_STATUS = OV.show_status !== false;      // status pill
  const SHRINK_IDLE_MS = 1000;
  const LOG_W = OV.log_width || 360;
  const LOG_H = OV.log_height || 300;
  const LOG_AUTOSCROLL = OV.log_autoscroll !== false;
  let KEYWORDS = (AL.keywords || []).map((k) => String(k).toLowerCase()).filter(Boolean);
  const ALERT_SOUND = AL.sound !== false;
  const ALERT_COLOR = AL.highlight || "#f04747";
  const LOG_MAX = 800;

  const icon = (name) => svgIcon(name, "vt-ico");
  const EVENT = OVERLAY_EVENT;

  // remove any prior overlay styles (incl. old un-id'd ones with the stale fade mask)
  document.querySelectorAll("style").forEach((s) => { if (s.id !== "vt-style" && /\.vt-container\s*\{/.test(s.textContent || "")) s.remove(); });
  const oldStyle = document.getElementById("vt-style"); if (oldStyle) oldStyle.remove();
  const style = document.createElement("style"); style.id = "vt-style";
  style.textContent = `
    .vt-container{position:fixed;bottom:96px;left:50%;transform:translateX(-50%);z-index:99999;
      width:52%;max-width:820px;pointer-events:none;display:flex;flex-direction:column;gap:8px;font-family:gg sans,sans-serif}
    .vt-entry{display:flex;align-items:center;gap:12px;background:rgba(0,0,0,.82);border-radius:10px;padding:8px 12px;animation:vt-in .18s;transition:opacity .3s,transform .3s,box-shadow .2s;transform-origin:center bottom}
    .vt-entry.vt-alert{box-shadow:0 0 0 2px ${ALERT_COLOR},0 0 14px ${ALERT_COLOR}}
    .vt-avatar{width:34px;height:34px;border-radius:50%;flex:0 0 auto;background:#2b2d31}
    .vt-text{color:#fff;font-size:16px;line-height:1.3}
    .vt-name{font-weight:600;margin-right:6px}
    .vt-name .vt-ico{width:12px;height:12px;opacity:.82;vertical-align:-1px;margin-left:2px}
    .vt-cursor{display:inline-block;color:#9bb7ff;margin-left:2px;animation:vt-blink 1s steps(2,jump-none) infinite}
    @keyframes vt-blink{0%,49%{opacity:1}50%,100%{opacity:.12}}
    @keyframes vt-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

    .vtstat{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;line-height:1}
    .vt-dot{width:8px;height:8px;border-radius:50%;background:#43b581;flex:0 0 auto;animation:vt-pulse 1.8s infinite}
    .vtstat.off .vt-dot{background:#f04747;animation:none}
    .vtstat.warn .vt-dot{background:#faa61a;animation:none}
    @keyframes vt-pulse{0%{box-shadow:0 0 0 0 rgba(67,181,129,.5)}70%{box-shadow:0 0 0 6px rgba(67,181,129,0)}100%{box-shadow:0 0 0 0 rgba(67,181,129,0)}}
    .vt-status{position:fixed;bottom:64px;left:50%;transform:translateX(-50%);z-index:99999;
      background:rgba(24,25,28,.9);border:1px solid rgba(255,255,255,.08);border-radius:13px;padding:4px 11px;
      font-family:gg sans,sans-serif;font-size:11px;color:#e3e5e8;pointer-events:none}

    .vtlog{position:fixed;top:64px;right:12px;width:360px;z-index:99999;display:flex;flex-direction:column;
      resize:both;overflow:hidden;min-width:240px;max-width:72vw;min-height:140px;max-height:85vh;
      background:rgba(24,25,28,.93);border:1px solid rgba(255,255,255,.08);border-radius:10px;
      font-family:gg sans,sans-serif;color:#e3e5e8;box-shadow:0 8px 28px rgba(0,0,0,.5);pointer-events:auto}
    .vtlog-head{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:move;border-bottom:1px solid rgba(255,255,255,.07);user-select:none}
    .vtlog-title{font-weight:600;font-size:13px}
    .vtlog-head .vtstat{font-size:11px;color:#b5bac1;flex:1}
    .vtlog-btn{cursor:pointer;font-size:12px;color:#b5bac1;background:rgba(255,255,255,.06);border-radius:5px;padding:2px 7px}
    .vtlog-btn:hover{background:rgba(255,255,255,.14);color:#fff}
    .vtlog-body{overflow-y:auto;padding:6px 10px;font-size:13px;line-height:1.4;scrollbar-width:thin;scrollbar-color:#3f4248 transparent;flex:1 1 auto;min-height:0}
    .vtlog-body::-webkit-scrollbar{width:10px}
    .vtlog-body::-webkit-scrollbar-track{background:transparent}
    .vtlog-body::-webkit-scrollbar-thumb{background:#3f4248;border-radius:8px;border:2px solid transparent;background-clip:padding-box}
    .vtlog-body::-webkit-scrollbar-thumb:hover{background:#4f535b;background-clip:padding-box}
    .vtlog.collapsed .vtlog-body,.vtlog.collapsed .vtlog-jump{display:none}
    .vtlog.collapsed{height:auto!important;min-height:0;resize:none}
    .vtlog.collapsed .vtlog-head{border-bottom:0}
    .vtlog-jump{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);cursor:pointer;display:none;
      background:#5865f2;color:#fff;font-size:11px;font-weight:600;border-radius:999px;padding:4px 11px;box-shadow:0 2px 10px rgba(0,0,0,.5)}
    .vtl{display:flex;align-items:flex-start;gap:6px;margin:4px 0;padding-left:6px;border-left:2px solid transparent}
    .vtl.alert{border-left-color:${ALERT_COLOR}}
    .vtl-av{width:18px;height:18px;border-radius:50%;flex:0 0 auto;margin-top:2px;background:#2b2d31}
    .vtl-c{flex:1;min-width:0;word-wrap:break-word}
    .vtl-t{color:#72767d;font-size:11px;margin-right:5px}
    .vtl-n{font-weight:600;margin-right:5px}
    .vtl-n .vt-ico{width:12px;height:12px;opacity:.7;vertical-align:-1px;margin-left:2px}
    .vt-ico{width:14px;height:14px;flex:0 0 auto;vertical-align:-2px}
    .vtl-ev{display:flex;align-items:center;gap:6px;margin:3px 0;padding-left:6px;opacity:.78;font-size:12px;color:#b5bac1}
    .vtl-ev .vt-ico{margin-top:0}
    .vtl-ev-av{width:14px;height:14px;margin-top:0}
    .vtl-ev b{color:#dbdee1;font-weight:600}
    .vtl-ev .vtl-t{margin-right:0}
    .vt-text mark,.vtl mark{background:${ALERT_COLOR};color:#fff;border-radius:3px;padding:0 2px}
    .vt-text .vt-sound,.vtl .vt-sound{color:#9bb7ff;font-style:italic;opacity:.9}
    .vtl-n{cursor:default}
    .vt-assign{position:fixed;z-index:100000;width:240px;max-height:320px;overflow:auto;background:rgba(24,25,28,.98);
      border:1px solid rgba(255,255,255,.1);border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.6);padding:6px;
      font-family:gg sans,sans-serif;color:#e3e5e8;pointer-events:auto}
    .vt-assign .va-h{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#949ba4;padding:4px 6px}
    .vt-assign .va-list{display:flex;flex-direction:column}
    .vt-assign .va-item{display:flex;align-items:center;gap:8px;padding:6px;border-radius:6px;cursor:pointer}
    .vt-assign .va-item:hover{background:rgba(255,255,255,.08)}
    .vt-assign .va-item img{width:22px;height:22px;border-radius:50%;flex:0 0 22px;background:#2b2d31}
    .vt-assign .va-item span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
    .vt-assign .va-item small{margin-left:auto;color:#949ba4;font-size:10px;flex:0 0 auto}
    .vt-assign .va-stream{display:inline-flex;align-items:center}
    .vt-assign .va-stream .vt-ico{width:14px;height:14px}
    .vt-assign .va-empty{color:#949ba4;font-size:11px;padding:6px;line-height:1.45}
    .vt-assign .va-input{width:100%;margin:4px 0;background:#1e1f22;border:1px solid rgba(255,255,255,.1);color:#e3e5e8;border-radius:5px;padding:6px 8px;font:inherit;box-sizing:border-box}
    .vt-assign .va-clear{display:flex;align-items:center;gap:6px;padding:6px;color:#f0b232;cursor:pointer;border-radius:6px;font-size:12px}
    .vt-assign .va-clear:hover{background:rgba(255,255,255,.06)}
    .vt-assign .va-clear .vt-ico{width:14px;height:14px}`;
  document.head.appendChild(style);

  // whole-word keyword match, so "elly" doesn't fire on "belly"
  const _kwReCache = {};
  const kwRe = (k) => _kwReCache[k] || (_kwReCache[k] = keywordRegex(k));
  const matchKeyword = (t) => {
    if (!KEYWORDS.length || !t) return null;
    for (const k of KEYWORDS) { const re = kwRe(k); re.lastIndex = 0; if (re.test(t)) return k; }
    return null;
  };
  // Whisper sound events ([laughs], [LAUGHTER], (claps), *Nyuh*, ♪music♪) -> .vt-sound spans; the
  // surrounding speech still gets keyword <mark>s.
  const SOUND_RE = SOUND_EVENT_RE;
  function setText(el, text, kw) {
    el.textContent = "";
    const t = text || "";
    const re = kw ? kwRe(kw) : null;
    const emitSpeech = (chunk) => {
      if (!chunk) return;
      if (!re) { el.appendChild(document.createTextNode(chunk)); return; }
      re.lastIndex = 0; let i = 0, mch;
      while ((mch = re.exec(chunk)) !== null) {
        if (mch.index > i) el.appendChild(document.createTextNode(chunk.slice(i, mch.index)));
        const m = document.createElement("mark"); m.textContent = mch[0]; el.appendChild(m);
        i = mch.index + mch[0].length;
        if (mch[0].length === 0) re.lastIndex++;
      }
      if (i < chunk.length) el.appendChild(document.createTextNode(chunk.slice(i)));
    };
    let i = 0, m; SOUND_RE.lastIndex = 0;
    while ((m = SOUND_RE.exec(t)) !== null) {
      if (m.index > i) emitSpeech(t.slice(i, m.index));
      const s = document.createElement("span"); s.className = "vt-sound"; s.textContent = m[0];
      el.appendChild(s);
      i = m.index + m[0].length;
      if (m[0].length === 0) SOUND_RE.lastIndex++;
    }
    if (i < t.length) emitSpeech(t.slice(i));
  }
  function beep() {
    if (!ALERT_SOUND) return;
    try {
      const ac = window.__vtAC || (window.__vtAC = new (window.AudioContext || window.webkitAudioContext)());
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = "sine"; o.frequency.value = 880; o.connect(g); g.connect(ac.destination);
      g.gain.setValueAtTime(0.0001, ac.currentTime);
      g.gain.exponentialRampToValueAtTime(0.15, ac.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.35);
      o.start(); o.stop(ac.currentTime + 0.36);
    } catch (e) {}
  }

  // ---- transient subtitles ----
  const container = document.createElement("div"); container.className = "vt-container";
  if (SHOW_SUBS) document.body.appendChild(container);
  const blocks = new Map(), order = [];
  let layoutTimer = null;
  function setNameLabel(el, name, locked, stream, suffix) {
    el.textContent = name || "unknown";
    if (stream) el.insertAdjacentHTML("beforeend", " " + icon("screen-share"));
    if (locked) el.insertAdjacentHTML("beforeend", " " + icon("lock"));
    if (suffix) el.appendChild(document.createTextNode(suffix));
  }
  function scheduleLayout(delay) {
    if (layoutTimer) clearTimeout(layoutTimer);
    layoutTimer = setTimeout(() => { layoutTimer = null; updateLayout(); }, Math.max(0, delay));
  }
  // Keep subtitles STABLE: a speaker keeps the slot they first appeared in and updates in place,
  // instead of being yanked to the bottom on every word (which made the stack jump around).
  function touchBlock(uid, b) {
    b.lastActive = Date.now();
  }
  // FLIP: when a block is added/removed and the others shift, animate them from their old position
  // to the new one so the eye can follow the movement instead of it snapping.
  function withFlip(mutate) {
    const first = new Map();
    for (const uid of order) { const b = blocks.get(uid); if (b && b.el) first.set(uid, b.el.offsetTop); }
    mutate();
    for (const uid of order) {
      const b = blocks.get(uid); if (!b || !b.el || !first.has(uid)) continue;
      const dy = first.get(uid) - b.el.offsetTop;
      if (!dy) continue;
      const rest = b.el.style.transform || "";
      b.el.style.transition = "none";
      b.el.style.transform = "translateY(" + dy + "px) " + rest;
      void b.el.offsetHeight;                                     // reflow with transition off (invert)
      b.el.style.transition = "transform .3s cubic-bezier(.2,.7,.3,1), opacity .3s, box-shadow .2s";
      requestAnimationFrame(() => { b.el.style.transform = rest; });   // play to resting position
    }
  }
  function updateLayout() {
    if (layoutTimer) { clearTimeout(layoutTimer); layoutTimer = null; }
    const n = order.length, now = Date.now();
    let nextLayout = null;
    for (let idx = 0; idx < n; idx++) {
      const b = blocks.get(order[idx]); if (!b) continue;
      const fromBottom = n - 1 - idx;
      const quietFor = now - (b.lastActive || 0);
      const isQuiet = fromBottom > 0 && quietFor >= SHRINK_IDLE_MS;
      if (fromBottom > 0 && !isQuiet) {
        const due = SHRINK_IDLE_MS - quietFor + 20;
        nextLayout = nextLayout == null ? due : Math.min(nextLayout, due);
      }
      let op = 1;
      if (isQuiet && n >= FADE_START && fromBottom >= FADE_START - 1) op = Math.max(MIN_FADE, 1 - (fromBottom - FADE_START + 2) * 0.28);
      b.el.style.opacity = String(op);
      const scale = SHRINK_QUIET && isQuiet ? Math.max(0.62, 1 - fromBottom * 0.12) : 1;
      b.el.style.transform = scale < 0.999 ? "scale(" + scale.toFixed(3) + ")" : "";
    }
    if (nextLayout != null) scheduleLayout(nextLayout);
  }
  function removeBlk(uid) {
    const b = blocks.get(uid); if (!b) return;
    if (b.timeout) clearTimeout(b.timeout);
    const el = b.el;
    withFlip(() => {
      // lift the leaving entry out of the flex flow so the survivors reflow up NOW (FLIP animates
      // them); the leaving entry then fades away in place where it sat.
      const cr = container.getBoundingClientRect(), r = el.getBoundingClientRect();
      el.style.top = (r.top - cr.top) + "px";
      el.style.left = (r.left - cr.left) + "px";
      el.style.width = r.width + "px";
      el.style.position = "absolute";
      el.style.margin = "0";
      el.style.pointerEvents = "none";
      blocks.delete(uid); const i = order.indexOf(uid); if (i >= 0) order.splice(i, 1);
      updateLayout();          // settle survivors' fade/scale first; FLIP then plays to that resting state
    });
    el.style.transition = "opacity .3s ease, transform .3s ease";
    requestAnimationFrame(() => { el.style.opacity = "0"; el.style.transform = "translateY(-6px) scale(.96)"; });
    setTimeout(() => el.remove(), 320);
  }
  function sub(uid, name, avatar, text, isFinal, stream) {
    if (!SHOW_SUBS) return;
    let b = blocks.get(uid);
    if (b && b.finalized) {
      // A previous utterance for this speaker ended. Either MERGE the new one onto it (keep the
      // last bubble's context instead of yanking it) or start fresh, based on how soon the new
      // utterance arrives and how much we've already gathered. Shorter so far -> longer grace (join
      // eagerly); longer -> only a brief grace; past a cap -> always start fresh.
      const gap = Date.now() - (b.finalizedAt || 0);
      const grace = Math.max(1500, 5000 - (b.committed || "").length * 25);
      if (MERGE_SUBS && gap <= grace && (b.committed || "").length <= 280) b.finalized = false;  // continue same bubble
      else { removeBlk(uid); b = null; }
    }
    if (!b) {
      while (order.length >= MAX_BLOCKS) removeBlk(order[0]);
      const el = document.createElement("div"); el.className = "vt-entry";
      const img = document.createElement("img"); img.className = "vt-avatar"; if (avatar) img.src = avatar;
      img.onerror = () => { img.src = emojiAvatar(uid); };           // real avatar failed -> emoji
      const t = document.createElement("div"); t.className = "vt-text";
      const nm = document.createElement("span"); nm.className = "vt-name"; setNameLabel(nm, name, false, stream, ""); nm.style.color = colorFor(uid);
      const body = document.createElement("span");
      const cur = document.createElement("span"); cur.className = "vt-cursor"; cur.textContent = "▍";
      t.append(nm, body, cur); el.append(img, t); container.appendChild(el);
      b = { el, body, cur, nm, img, timeout: null, finalized: false, alerted: false, lastActive: 0, committed: "", finalizedAt: 0 }; blocks.set(uid, b); order.push(uid);
    }
    touchBlock(uid, b);
    if (name) setNameLabel(b.nm, name, false, stream, "");
    if (avatar && b.img && !b.img.src) b.img.src = avatar;
    // committed = earlier finalized utterances in this merge run; live = the in-progress one
    const shown = ((b.committed ? b.committed + " " : "") + (text || "")).trim();
    b.text = shown;
    const kw = matchKeyword(shown);
    setText(b.body, shown, kw);
    if (kw && !b.alerted) { b.alerted = true; b.el.classList.add("vt-alert"); beep(); }
    if (isFinal) { b.committed = shown; b.finalizedAt = Date.now(); }   // fold this utterance into the run
    b.finalized = !!isFinal; b.cur.style.display = b.finalized ? "none" : "inline-block";
    if (b.timeout) clearTimeout(b.timeout);
    b.timeout = setTimeout(() => removeBlk(uid), LIVE_MS);
    updateLayout();
  }

  // ---- transcript log ----
  const panel = document.createElement("div"); panel.className = "vtlog";
  panel.innerHTML = `<div class="vtlog-head"><span class="vtlog-title">Transcript</span>
      <span class="vtstat off" data-hstat><span class="vt-dot"></span><span data-htext>…</span></span>
      <span class="vtlog-btn" data-act="copy">copy</span><span class="vtlog-btn" data-act="clear">clear</span>
      <span class="vtlog-btn" data-act="toggle">▾</span></div><div class="vtlog-body"></div>
      <div class="vtlog-jump">Jump to latest ↓</div>`;
  if (SHOW_LOG) document.body.appendChild(panel);
  const logBody = panel.querySelector(".vtlog-body"), head = panel.querySelector(".vtlog-head");
  const logJump = panel.querySelector(".vtlog-jump");
  const hstat = panel.querySelector("[data-hstat]"), htext = panel.querySelector("[data-htext]");
  const history = [];
  panel.style.width = LOG_W + "px";                     // configurable; panel is drag-resizable (both axes)
  panel.style.height = (LOG_H + 38) + "px";             // +head; body flexes to fill, so height drag works
  let pinned = true, lastAuto = 0, jumpTimer = null;
  const atBottom = () => logBody.scrollHeight - logBody.scrollTop - logBody.clientHeight < 28;
  const stickLog = () => {                               // scroll to bottom, then re-apply next frame
    lastAuto = Date.now(); logBody.scrollTop = logBody.scrollHeight;
    requestAnimationFrame(() => { if (pinned) { lastAuto = Date.now(); logBody.scrollTop = logBody.scrollHeight; } });
  };
  logBody.addEventListener("scroll", () => {
    if (Date.now() - lastAuto < 130) return;            // ignore our own auto-scroll (no flicker)
    if (atBottom()) { pinned = true; logJump.style.display = "none"; if (jumpTimer) { clearTimeout(jumpTimer); jumpTimer = null; } }
    else { pinned = false; if (!jumpTimer) jumpTimer = setTimeout(() => { if (!pinned) logJump.style.display = "block"; jumpTimer = null; }, 180); }
  });
  logJump.addEventListener("click", () => { pinned = true; logJump.style.display = "none"; lastAuto = Date.now(); logBody.scrollTop = logBody.scrollHeight; });
  function log(name, uid, avatar, text, ts, locked, stream) {
    if (!SHOW_LOG) return;
    const kw = matchKeyword(text);
    history.push({ name, text, ts, alert: !!kw });
    while (history.length > LOG_MAX) history.shift();
    const row = document.createElement("div"); row.className = "vtl" + (kw ? " alert" : "");
    row.dataset.uid = uid;
    const av = document.createElement("img"); av.className = "vtl-av"; av.src = avatar || emojiAvatar(uid);
    av.onerror = () => { av.src = emojiAvatar(uid); };                 // real avatar failed -> emoji
    av.onload = () => { if (LOG_AUTOSCROLL && pinned) stickLog(); };   // keep pinned once avatar lays out
    const c = document.createElement("div"); c.className = "vtl-c";
    const t = document.createElement("span"); t.className = "vtl-t"; t.textContent = new Date(ts).toLocaleTimeString([], { hour12: false });
    const n = document.createElement("span"); n.className = "vtl-n"; setNameLabel(n, (kw ? "🔔 " : "") + (name || "unknown"), locked, stream, ":"); n.style.color = colorFor(uid);
    n.title = "Click to reassign"; n.style.cursor = "pointer";
    n.onclick = (e) => { e.stopPropagation(); openAssign(uid, n); };   // assignable, same picker as the desktop
    const b = document.createElement("span"); b.className = "vtl-tx"; b.dataset.text = text || ""; setText(b, text, kw);
    c.append(t, n, b); row.append(av, c); logBody.appendChild(row);
    while (logBody.children.length > LOG_MAX) logBody.removeChild(logBody.firstChild);
    if (LOG_AUTOSCROLL && pinned) stickLog();
  }
  function logEvent(name, uid, event, ts, avatar) {
    if (!SHOW_LOG) return;
    const meta = EVENT[event]; if (!meta) return;
    const row = document.createElement("div"); row.className = "vtl-ev";
    const t = document.createElement("span"); t.className = "vtl-t"; t.textContent = new Date(ts).toLocaleTimeString([], { hour12: false });
    const ic = document.createElement("span"); ic.innerHTML = icon(meta[0]); ic.style.color = meta[2];
    const tx = document.createElement("span");
    const b = document.createElement("b"); b.textContent = name || "someone"; b.style.color = colorFor(uid);
    tx.append(b, document.createTextNode(" " + meta[1]));
    if (avatar) {                                       // show the user's avatar on the event row
      const av = document.createElement("img"); av.className = "vtl-av vtl-ev-av"; av.src = avatar;
      av.onerror = () => { av.style.display = "none"; };
      av.onload = () => { if (LOG_AUTOSCROLL && pinned) stickLog(); };
      row.append(t, av, ic, tx);
    } else { row.append(t, ic, tx); }
    logBody.appendChild(row);
    while (logBody.children.length > LOG_MAX) logBody.removeChild(logBody.firstChild);
    if (LOG_AUTOSCROLL && pinned) stickLog();
  }
  // ---- assign / reassign: the same picker as the desktop, driven over the relay ----
  let roster = [];                                  // this client's call members, pushed by the engine
  let assignPop = null;
  const closeAssign = () => { if (assignPop) { assignPop.remove(); assignPop = null; } };
  const sendAssign = (src, payload) => {
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify(Object.assign({ type: "assign", src: src }, payload))); } catch (e) {}
  };
  function openAssign(src, anchor) {
    closeAssign();
    const pop = document.createElement("div"); pop.className = "vt-assign";
    const head = document.createElement("div"); head.className = "va-h"; head.textContent = "Assign speaker"; pop.appendChild(head);
    const members = roster.slice().sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
    if (members.length) {
      const list = document.createElement("div"); list.className = "va-list";
      members.forEach((u) => {
        const it = document.createElement("div"); it.className = "va-item";
        const im = document.createElement("img"); im.src = u.avatar || emojiAvatar(u.userId);
        const sp = document.createElement("span"); sp.textContent = u.name || "user";
        it.append(im, sp);
        if (u.stream) {
          const sm = document.createElement("small"); sm.className = "va-stream"; sm.title = "Streaming";
          sm.innerHTML = icon("screen-share"); it.appendChild(sm);
        }
        it.onclick = () => { sendAssign(src, { userId: u.userId }); closeAssign(); };
        list.appendChild(it);
      });
      pop.appendChild(list);
    } else {
      const em = document.createElement("div"); em.className = "va-empty";
      em.textContent = "No call roster (this client has no debug port). Type a name or paste a user ID.";
      pop.appendChild(em);
    }
    const input = document.createElement("input"); input.className = "va-input"; input.placeholder = "name or user ID";
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return; const v = input.value.trim(); if (!v) return;
      sendAssign(src, /^\d{15,21}$/.test(v) ? { userId: v } : { name: v }); closeAssign();
    });
    pop.appendChild(input);
    const clr = document.createElement("div"); clr.className = "va-clear"; clr.innerHTML = icon("lock-open") + " Clear (auto-detect)";
    clr.onclick = () => { sendAssign(src, { clear: true }); closeAssign(); };
    pop.appendChild(clr);
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect(); const w = pop.offsetWidth, h = pop.offsetHeight;
    let left = r.left, top = r.bottom + 4;
    if (left + w > innerWidth - 8) left = innerWidth - w - 8;
    if (top + h > innerHeight - 8) top = r.top - h - 4;
    pop.style.left = Math.max(8, left) + "px"; pop.style.top = Math.max(8, top) + "px";
    pop.addEventListener("click", (e) => e.stopPropagation());
    setTimeout(() => { try { input.focus(); } catch (e) {} }, 0);
    assignPop = pop;
  }
  document.addEventListener("click", closeAssign);

  // re-apply keyword highlighting to everything already on screen after a live keyword edit
  function rehighlight() {
    blocks.forEach((b) => { const kw = matchKeyword(b.text); setText(b.body, b.text || "", kw); if (kw && !b.alerted) { b.alerted = true; b.el.classList.add("vt-alert"); } });
    logBody.querySelectorAll(".vtl-tx").forEach((tx) => {
      const text = tx.dataset.text || ""; const kw = matchKeyword(text);
      setText(tx, text, kw);
      const row = tx.closest(".vtl"); if (row) row.classList.toggle("alert", !!kw);
      const n = row && row.querySelector(".vtl-n");
      if (n) { const base = n.textContent.replace(/^🔔 /, ""); n.textContent = (kw ? "🔔 " : "") + base; }
    });
  }
  head.addEventListener("click", (e) => {
    const act = e.target.getAttribute("data-act");
    if (act === "toggle") { panel.classList.toggle("collapsed"); e.target.textContent = panel.classList.contains("collapsed") ? "▸" : "▾"; }
    else if (act === "clear") { logBody.innerHTML = ""; history.length = 0; }
    else if (act === "copy") navigator.clipboard && navigator.clipboard.writeText(history.map((h) => `[${new Date(h.ts).toLocaleTimeString([], { hour12: false })}] ${h.name}: ${h.text}`).join("\n"));
  });
  (() => { let sx, sy, ox, oy, drag = false;
    head.addEventListener("mousedown", (e) => { if (e.target.classList.contains("vtlog-btn")) return; drag = true; sx = e.clientX; sy = e.clientY; const r = panel.getBoundingClientRect(); ox = r.left; oy = r.top; panel.style.right = "auto"; e.preventDefault(); });
    window.addEventListener("mousemove", (e) => { if (!drag) return; panel.style.left = ox + e.clientX - sx + "px"; panel.style.top = oy + e.clientY - sy + "px"; });
    window.addEventListener("mouseup", () => (drag = false));
  })();

  // ---- status (bottom pill + log header) ----
  const statusEl = document.createElement("div"); statusEl.className = "vt-status vtstat off";
  statusEl.innerHTML = `<span class="vt-dot"></span><span data-stext>Connecting…</span>`;
  if (SHOW_STATUS) document.body.appendChild(statusEl);
  const stext = statusEl.querySelector("[data-stext]");
  let lastStatus = 0;
  function setStatus(cls, text) {
    statusEl.className = "vt-status vtstat" + (cls ? " " + cls : "");
    hstat.className = "vtstat" + (cls ? " " + cls : "");
    stext.textContent = text; htext.textContent = text;
  }
  const tick = setInterval(() => {
    const open = ws && ws.readyState === 1;
    if (!open) setStatus("off", "Disconnected");
    else if (Date.now() - lastStatus > 6000) setStatus("warn", "Waiting for backend…");
  }, 1500);

  function handle(m) {
    if (!m) return;
    if (m.type === "status") {
      lastStatus = Date.now();
      // scope the "n speaking" count to THIS overlay's own client, not the global total
      let n = m.active || 0;
      if (CLIENT && m.clients && m.clients[CLIENT]) n = m.clients[CLIENT].active || 0;
      setStatus("", n > 0 ? "Listening · " + n + " speaking" : "Listening");
      return;
    }
    if (m.type === "keywords") {              // live keyword edit from the desktop UI (global)
      KEYWORDS = (m.keywords || []).map((k) => String(k).toLowerCase()).filter(Boolean);
      rehighlight();
      return;
    }
    if (CLIENT && m.client && m.client !== CLIENT) return;   // ignore other clients' calls
    if (m.type === "roster") { roster = m.members || []; return; }   // this client's members, for the picker
    if (m.type === "event") { logEvent(m.name, m.userId, m.event, m.ts || Date.now(), m.avatar); return; }
    if (m.type === "keepalive") {
      // the speaker is still talking even if this chunk had no words; keep the subtitle alive
      const b = blocks.get(m.userId);
      if (b) {
        touchBlock(m.userId, b);
        if (b.timeout) clearTimeout(b.timeout);
        b.timeout = setTimeout(() => removeBlk(m.userId), LIVE_MS);
        updateLayout();
      }
      return;
    }
    if (m.type === "rename") {
      const d = speakerDisplay(m);
      const esc = (window.CSS && CSS.escape) ? CSS.escape(m.userId) : m.userId;
      document.querySelectorAll('.vtl[data-uid="' + esc + '"]').forEach((r) => {
        const n = r.querySelector(".vtl-n");
        if (n) setNameLabel(n, d.name, d.locked, d.stream, ":");
        const a = r.querySelector(".vtl-av"); if (a) a.src = d.avatar;
      });
      const b = blocks.get(m.userId);                       // also update an on-screen subtitle
      if (b) { if (b.nm) setNameLabel(b.nm, d.name, false, d.stream, ""); if (b.img) b.img.src = d.avatar; }
      return;
    }
    if (m.type !== "transcript") return;
    if (m.isFinal && !m.text) {        // utterance cut off with nothing to show -> expire it promptly
      const b = blocks.get(m.userId);
      // ...but if we've merged real text already, keep it around (and mergeable) for the normal
      // lifetime instead of wiping a populated bubble in 400 ms.
      if (b) {
        b.finalized = true; b.finalizedAt = Date.now();
        if (b.timeout) clearTimeout(b.timeout);
        const keep = MERGE_SUBS && (b.committed || "").length > 0;
        b.timeout = setTimeout(() => removeBlk(m.userId), keep ? LIVE_MS : 400);
      }
      return;
    }
    const d = speakerDisplay(m);
    sub(m.userId, d.name, d.avatar, m.text, m.isFinal, d.stream);
    if (m.isFinal && m.text) log(d.name, m.userId, d.avatar, m.text, m.ts || Date.now(), d.locked, d.stream);
  }

  let ws = null, stopped = false;
  function connect() {
    if (stopped) return;
    try { if (ws && ws.readyState <= 1) return; } catch (_) {}
    setStatus("warn", "Connecting…");
    ws = new WebSocket(RELAY);
    ws.onopen = () => setStatus("warn", "Waiting for backend…");
    ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (_) {} };
    ws.onclose = () => { setStatus("off", "Disconnected"); if (!stopped) setTimeout(connect, 3000); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }
  connect();
  function destroy() {
    stopped = true; clearInterval(tick);
    try { ws && ws.close(); } catch (_) {}
    [container, panel, statusEl].forEach((e) => e && e.remove());
    const s = document.getElementById("vt-style"); if (s) s.remove();
    delete window.__vtOverlay;
  }
  window.__vtOverlay = { connect, feed: handle, destroy, clear: () => order.slice().forEach(removeBlk) };
  return "injected";
})();

