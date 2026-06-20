// BetterDiscord-free overlay injected into Discord's renderer via CDP.
// Subtitles (count-based fade + live cursor + keyword alerts) + scrollable transcript log + status pills.
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
  const LOG_H = OV.log_height || 300;
  const LOG_AUTOSCROLL = OV.log_autoscroll !== false;
  let KEYWORDS = (AL.keywords || []).map((k) => String(k).toLowerCase()).filter(Boolean);
  const ALERT_SOUND = AL.sound !== false;
  const ALERT_COLOR = AL.highlight || "#f04747";
  const LOG_MAX = 800;

  // ---- inline Lucide icons (offline; 24x24 stroke) ----
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
  };
  const icon = (name) => '<svg class="vt-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (LU[name] || "") + "</svg>";
  const EVENT = {
    joined: ["log-in", "joined", "#43b581"], left: ["log-out", "left", "#f04747"],
    muted: ["mic-off", "muted", "#b5bac1"], unmuted: ["mic", "unmuted", "#43b581"],
    deafened: ["volume-x", "deafened", "#b5bac1"], undeafened: ["volume-2", "undeafened", "#43b581"],
    video_on: ["video", "turned camera on", "#5865f2"], video_off: ["video-off", "turned camera off", "#b5bac1"],
    stream_on: ["screen-share", "started streaming", "#5865f2"], stream_off: ["screen-share-off", "stopped streaming", "#b5bac1"],
  };

  // remove any prior overlay styles (incl. old un-id'd ones with the stale fade mask)
  document.querySelectorAll("style").forEach((s) => { if (s.id !== "vt-style" && /\.vt-container\s*\{/.test(s.textContent || "")) s.remove(); });
  const oldStyle = document.getElementById("vt-style"); if (oldStyle) oldStyle.remove();
  const style = document.createElement("style"); style.id = "vt-style";
  style.textContent = `
    .vt-container{position:fixed;bottom:96px;left:50%;transform:translateX(-50%);z-index:99999;
      width:52%;max-width:820px;pointer-events:none;display:flex;flex-direction:column;gap:8px;font-family:gg sans,sans-serif}
    .vt-entry{display:flex;align-items:center;gap:12px;background:rgba(0,0,0,.82);border-radius:10px;padding:8px 12px;animation:vt-in .18s;transition:opacity .3s,box-shadow .2s}
    .vt-entry.vt-alert{box-shadow:0 0 0 2px ${ALERT_COLOR},0 0 14px ${ALERT_COLOR}}
    .vt-avatar{width:34px;height:34px;border-radius:50%;flex:0 0 auto;background:#2b2d31}
    .vt-text{color:#fff;font-size:16px;line-height:1.3}
    .vt-name{font-weight:600;margin-right:6px}
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
      background:rgba(24,25,28,.93);border:1px solid rgba(255,255,255,.08);border-radius:10px;
      font-family:gg sans,sans-serif;color:#e3e5e8;box-shadow:0 8px 28px rgba(0,0,0,.5);pointer-events:auto}
    .vtlog-head{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:move;border-bottom:1px solid rgba(255,255,255,.07);user-select:none}
    .vtlog-title{font-weight:600;font-size:13px}
    .vtlog-head .vtstat{font-size:11px;color:#b5bac1;flex:1}
    .vtlog-btn{cursor:pointer;font-size:12px;color:#b5bac1;background:rgba(255,255,255,.06);border-radius:5px;padding:2px 7px}
    .vtlog-btn:hover{background:rgba(255,255,255,.14);color:#fff}
    .vtlog-body{overflow-y:auto;padding:6px 10px;font-size:13px;line-height:1.4;scrollbar-width:thin;resize:vertical;min-height:90px}
    .vtlog-body::-webkit-scrollbar{width:8px}.vtlog-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.16);border-radius:4px}
    .vtlog.collapsed .vtlog-body,.vtlog.collapsed .vtlog-jump{display:none}
    .vtlog-jump{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);cursor:pointer;display:none;
      background:#5865f2;color:#fff;font-size:11px;font-weight:600;border-radius:999px;padding:4px 11px;box-shadow:0 2px 10px rgba(0,0,0,.5)}
    .vtl{display:flex;align-items:flex-start;gap:6px;margin:4px 0;padding-left:6px;border-left:2px solid transparent}
    .vtl.alert{border-left-color:${ALERT_COLOR}}
    .vtl-av{width:18px;height:18px;border-radius:50%;flex:0 0 auto;margin-top:2px;background:#2b2d31}
    .vtl-c{flex:1;min-width:0;word-wrap:break-word}
    .vtl-t{color:#72767d;font-size:11px;margin-right:5px}
    .vtl-n{font-weight:600;margin-right:5px}
    .vt-ico{width:14px;height:14px;flex:0 0 auto;vertical-align:-2px}
    .vtl-ev{display:flex;align-items:center;gap:6px;margin:3px 0;padding-left:6px;opacity:.78;font-size:12px;color:#b5bac1}
    .vtl-ev .vt-ico{margin-top:0}
    .vtl-ev-av{width:14px;height:14px;margin-top:0}
    .vtl-ev b{color:#dbdee1;font-weight:600}
    .vtl-ev .vtl-t{margin-right:0}
    .vt-text mark,.vtl mark{background:${ALERT_COLOR};color:#fff;border-radius:3px;padding:0 2px}`;
  document.head.appendChild(style);

  const colorFor = (id) => { let h = 0; for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `hsl(${h % 360},65%,72%)`; };
  const matchKeyword = (t) => { if (!KEYWORDS.length || !t) return null; const l = t.toLowerCase(); for (const k of KEYWORDS) if (l.includes(k)) return k; return null; };
  function setText(el, text, kw) {
    el.textContent = "";
    if (!kw) { el.textContent = text; return; }
    const low = text.toLowerCase(); let i = 0;
    while (true) {
      const j = low.indexOf(kw, i);
      if (j < 0) { el.appendChild(document.createTextNode(text.slice(i))); break; }
      el.appendChild(document.createTextNode(text.slice(i, j)));
      const m = document.createElement("mark"); m.textContent = text.slice(j, j + kw.length); el.appendChild(m);
      i = j + kw.length;
    }
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
  document.body.appendChild(container);
  const blocks = new Map(), order = [];
  function updateFades() {
    const n = order.length;
    for (let idx = 0; idx < n; idx++) {
      const b = blocks.get(order[idx]); if (!b) continue;
      const fromBottom = n - 1 - idx;
      let op = 1;
      if (n >= FADE_START && fromBottom >= FADE_START - 1) op = Math.max(MIN_FADE, 1 - (fromBottom - FADE_START + 2) * 0.28);
      b.el.style.opacity = String(op);
    }
  }
  function removeBlk(uid) {
    const b = blocks.get(uid); if (!b) return;
    if (b.timeout) clearTimeout(b.timeout);
    b.el.style.opacity = "0"; setTimeout(() => b.el.remove(), 300);
    blocks.delete(uid); const i = order.indexOf(uid); if (i >= 0) order.splice(i, 1);
    updateFades();
  }
  function sub(uid, name, avatar, text, isFinal) {
    let b = blocks.get(uid);
    if (b && b.finalized) { removeBlk(uid); b = null; }
    if (!b) {
      while (order.length >= MAX_BLOCKS) removeBlk(order[0]);
      const el = document.createElement("div"); el.className = "vt-entry";
      const img = document.createElement("img"); img.className = "vt-avatar"; if (avatar) img.src = avatar;
      const t = document.createElement("div"); t.className = "vt-text";
      const nm = document.createElement("span"); nm.className = "vt-name"; nm.textContent = name || "unknown"; nm.style.color = colorFor(uid);
      const body = document.createElement("span");
      const cur = document.createElement("span"); cur.className = "vt-cursor"; cur.textContent = "▍";
      t.append(nm, body, cur); el.append(img, t); container.appendChild(el);
      b = { el, body, cur, nm, img, timeout: null, finalized: false, alerted: false }; blocks.set(uid, b); order.push(uid);
    }
    if (name) b.nm.textContent = name;
    if (avatar && b.img && !b.img.src) b.img.src = avatar;
    b.text = text || "";
    const kw = matchKeyword(text);
    setText(b.body, text || "", kw);
    if (kw && !b.alerted) { b.alerted = true; b.el.classList.add("vt-alert"); beep(); }
    b.finalized = !!isFinal; b.cur.style.display = b.finalized ? "none" : "inline-block";
    if (b.timeout) clearTimeout(b.timeout);
    b.timeout = setTimeout(() => removeBlk(uid), LIVE_MS);
    updateFades();
  }

  // ---- transcript log ----
  const panel = document.createElement("div"); panel.className = "vtlog";
  panel.innerHTML = `<div class="vtlog-head"><span class="vtlog-title">Transcript</span>
      <span class="vtstat off" data-hstat><span class="vt-dot"></span><span data-htext>…</span></span>
      <span class="vtlog-btn" data-act="copy">copy</span><span class="vtlog-btn" data-act="clear">clear</span>
      <span class="vtlog-btn" data-act="toggle">▾</span></div><div class="vtlog-body"></div>
      <div class="vtlog-jump">Jump to latest ↓</div>`;
  document.body.appendChild(panel);
  const logBody = panel.querySelector(".vtlog-body"), head = panel.querySelector(".vtlog-head");
  const logJump = panel.querySelector(".vtlog-jump");
  const hstat = panel.querySelector("[data-hstat]"), htext = panel.querySelector("[data-htext]");
  const history = [];
  logBody.style.height = LOG_H + "px";                  // configurable, drag-resizable
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
  function log(name, uid, avatar, text, ts) {
    const kw = matchKeyword(text);
    history.push({ name, text, ts, alert: !!kw });
    while (history.length > LOG_MAX) history.shift();
    const row = document.createElement("div"); row.className = "vtl" + (kw ? " alert" : "");
    row.dataset.uid = uid;
    const av = document.createElement("img"); av.className = "vtl-av"; if (avatar) av.src = avatar;
    av.onload = () => { if (LOG_AUTOSCROLL && pinned) stickLog(); };   // keep pinned once avatar lays out
    const c = document.createElement("div"); c.className = "vtl-c";
    const t = document.createElement("span"); t.className = "vtl-t"; t.textContent = new Date(ts).toLocaleTimeString([], { hour12: false });
    const n = document.createElement("span"); n.className = "vtl-n"; n.textContent = (kw ? "🔔 " : "") + (name || "unknown") + ":"; n.style.color = colorFor(uid);
    const b = document.createElement("span"); b.className = "vtl-tx"; b.dataset.text = text || ""; setText(b, text, kw);
    c.append(t, n, b); row.append(av, c); logBody.appendChild(row);
    while (logBody.children.length > LOG_MAX) logBody.removeChild(logBody.firstChild);
    if (LOG_AUTOSCROLL && pinned) stickLog();
  }
  function logEvent(name, uid, event, ts, avatar) {
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
  document.body.appendChild(statusEl);
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
    if (m.type === "event") { logEvent(m.name, m.userId, m.event, m.ts || Date.now(), m.avatar); return; }
    if (m.type === "keepalive") {
      // the speaker is still talking even if this chunk had no words; keep the subtitle alive
      const b = blocks.get(m.userId);
      if (b) { if (b.timeout) clearTimeout(b.timeout); b.timeout = setTimeout(() => removeBlk(m.userId), LIVE_MS); }
      return;
    }
    if (m.type === "rename") {
      const esc = (window.CSS && CSS.escape) ? CSS.escape(m.userId) : m.userId;
      document.querySelectorAll('.vtl[data-uid="' + esc + '"]').forEach((r) => {
        const n = r.querySelector(".vtl-n"); if (n) n.textContent = (m.name || "unknown") + ":";
        const a = r.querySelector(".vtl-av"); if (a && m.avatar) a.src = m.avatar;
      });
      return;
    }
    if (m.type !== "transcript") return;
    if (m.isFinal && !m.text) {        // utterance cut off with nothing to show -> expire it promptly
      const b = blocks.get(m.userId);
      if (b) { b.finalized = true; if (b.timeout) clearTimeout(b.timeout); b.timeout = setTimeout(() => removeBlk(m.userId), 400); }
      return;
    }
    sub(m.userId, m.name, m.avatar, m.text, m.isFinal);
    if (m.isFinal && m.text) log(m.name || "unknown", m.userId, m.avatar, m.text, m.ts || Date.now());
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
