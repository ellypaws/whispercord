// Wrapper UI controller: talks to the Python js_api bridge + the relay WebSocket.
let API = null;          // window.pywebview.api once ready
let CFG = null;
let relay = null;
const panels = {};       // clientKey -> transcript pane state + retained history
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
const HISTORY_WINDOW = 200;
const HISTORY_STEP = 200;
const HISTORY_COLLAPSE_MS = 30000;
let itemSeq = 0;
let searchChips = [];
let searchSuggestItems = [];
let searchSuggestIndex = 0;

// ---------- inline Lucide icons (offline; 24x24 stroke) ----------
const LU = {
  "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  "calendar": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
  "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  "code-2": '<path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/>',
  "at-sign": '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
  "hash": '<line x1="4" x2="20" y1="9" y2="9"/><line x1="4" x2="20" y1="15" y2="15"/><line x1="10" x2="8" y1="3" y2="21"/><line x1="16" x2="14" y1="3" y2="21"/>',
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
  "check": '<polyline points="20 6 9 17 4 12"/>',
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
function setAnyHl(el, text, terms) {
  el.textContent = "";
  const t = text || "";
  const clean = Array.from(new Set((terms || []).map((x) => String(x || "").trim()).filter(Boolean)))
    .sort((a, b) => b.length - a.length);
  if (!clean.length) { el.textContent = t; return; }
  const re = new RegExp(clean.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  let i = 0, mch;
  while ((mch = re.exec(t)) !== null) {
    if (mch.index > i) el.appendChild(document.createTextNode(t.slice(i, mch.index)));
    const m = document.createElement("mark"); m.textContent = mch[0]; el.appendChild(m);
    i = mch.index + mch[0].length;
    if (mch[0].length === 0) re.lastIndex++;
  }
  if (i < t.length) el.appendChild(document.createTextNode(t.slice(i)));
}
// Whisper sound events ([laughs], [LAUGHTER], (claps), *Nyuh*, ♪music♪) rendered as highlighted
// .sound spans; the speech around them still gets keyword/search highlighting.
const SOUND_RE = /[\[(*♪][^\][()*♪]*[\])*♪]/g;
function renderLineText(el, text) {
  el.textContent = "";
  const terms = freeSearchTerms();
  const t = text || "";
  const emitSpeech = (chunk) => {
    if (!chunk) return;
    const tmp = document.createElement("span");
    if (terms.length) setAnyHl(tmp, chunk, terms);
    else setHl(tmp, chunk, matchKeyword(chunk));
    while (tmp.firstChild) el.appendChild(tmp.firstChild);
  };
  let i = 0, m; SOUND_RE.lastIndex = 0;
  while ((m = SOUND_RE.exec(t)) !== null) {
    if (m.index > i) emitSpeech(t.slice(i, m.index));
    const s = document.createElement("span"); s.className = "sound"; s.textContent = m[0];
    el.appendChild(s);
    i = m.index + m[0].length;
    if (m[0].length === 0) SOUND_RE.lastIndex++;
  }
  if (i < t.length) emitSpeech(t.slice(i));
}
// re-run highlighting over every transcript line after a keyword edit
function rehighlightAll() {
  renderAllPanels();
  // push the live edit to the in-Discord overlays through the relay control bus
  try { if (relay && relay.readyState === 1) relay.send(JSON.stringify({ type: "setKeywords", keywords: kwList })); } catch (e) {}
}

// ---------- transcript search + filter ----------
const SEARCH_OPS = new Set(["from", "before", "after", "during", "has", "mentions", "in"]);
const SEARCH_OPERATORS = [
  { op: "from", meta: "speaker", icon: "user" },
  { op: "after", meta: "date", icon: "calendar" },
  { op: "before", meta: "date", icon: "calendar" },
  { op: "during", meta: "date", icon: "calendar" },
  { op: "has", meta: "link, code", icon: "link" },
  { op: "mentions", meta: "name", icon: "at-sign" },
  { op: "in", meta: "client, voice, stream", icon: "hash" },
];
function stripQuotes(s) {
  s = String(s || "").trim();
  return ((s[0] === '"' && s[s.length - 1] === '"') || (s[0] === "'" && s[s.length - 1] === "'")) ? s.slice(1, -1) : s;
}
function lower(s) { return String(s || "").toLowerCase(); }
function parseFreeTerms(raw) {
  const out = [];
  const re = /"([^"]+)"|'([^']+)'|(\S+)/g;
  let m;
  while ((m = re.exec(raw || "")) !== null) {
    const v = (m[1] || m[2] || m[3] || "").trim();
    if (!v || /^(from|before|after|during|has|mentions|in):/i.test(v)) continue;
    out.push(v);
  }
  return out;
}
function freeSearchTerms() {
  const input = $("search-input");
  return parseFreeTerms(input ? input.value : "");
}
function searchActive() {
  const input = $("search-input");
  return searchChips.length > 0 || !!(input && input.value.trim());
}
function parseLooseDate(value) {
  const v = lower(stripQuotes(value));
  const now = new Date();
  let y = now.getFullYear(), m = null, d = null;
  if (v === "today") { m = now.getMonth() + 1; d = now.getDate(); }
  else if (v === "yesterday") {
    const dt = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    y = dt.getFullYear(); m = dt.getMonth() + 1; d = dt.getDate();
  } else {
    let match = v.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
    if (match) { y = parseInt(match[1], 10); m = parseInt(match[2], 10); d = parseInt(match[3], 10); }
    else {
      match = v.match(/^(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?$/);
      if (match) {
        m = parseInt(match[1], 10); d = parseInt(match[2], 10);
        if (match[3]) { y = parseInt(match[3], 10); if (y < 100) y += 2000; }
      }
    }
  }
  if (m == null || d == null) return null;
  const start = new Date(y, m - 1, d);
  if (start.getFullYear() !== y || start.getMonth() !== m - 1 || start.getDate() !== d) return null;
  const end = new Date(y, m - 1, d + 1);
  return { start: start.getTime(), end: end.getTime() };
}
function looksLikeLink(text) { return /\bhttps?:\/\/\S+|\bwww\.\S+/i.test(text || ""); }
function looksLikeCode(text) {
  return /`[^`]+`|```|<\/?[a-z][\s\S]*?>|\b(const|let|var|function|class|return|import|select|update|insert)\b|[A-Za-z_$][\w$]*\([^)]*\)|[{};]/i.test(text || "");
}
function displayNameFor(item) {
  const s = sources[item.userId] || {};
  if (s.resolved && s.name) return s.name;
  return item.name || unknownLabel(item.userId);
}
function speakerMatches(item, chip) {
  const ids = (chip.userIds || (chip.userId ? [chip.userId] : [])).map(lower).filter(Boolean);
  const needle = lower(chip.name || chip.value || chip.userId);
  const name = lower(displayNameFor(item));
  const uid = lower(item.userId);
  if (ids.length && ids.includes(uid)) return true;
  if (!needle) return !ids.length;
  return name.includes(needle) || uid.includes(needle);
}
function mentionMatches(text, chip) {
  const needle = lower(chip.name || chip.value);
  return !!needle && lower(text).includes(needle);
}
function channelMatches(item, chip) {
  const v = lower(chip.value);
  if (!v) return true;
  if (v === "stream" || v === "voice") return lower(item.kind) === v;
  return lower(item.client).includes(v) || lower(clientLabel(item.client)).includes(v);
}
function chipMatches(item, chip) {
  const text = item.text || "";
  if (chip.op === "from") return speakerMatches(item, chip);
  if (chip.op === "mentions") return mentionMatches(text, chip);
  if (chip.op === "has") return lower(chip.value) === "link" ? looksLikeLink(text) : looksLikeCode(text);
  if (chip.op === "in") return channelMatches(item, chip);
  if (chip.op === "before" || chip.op === "after" || chip.op === "during") {
    const r = parseLooseDate(chip.value); if (!r) return true;
    const ts = item.ts || 0;
    if (chip.op === "before") return ts < r.start;
    if (chip.op === "after") return ts >= r.start;
    return ts >= r.start && ts < r.end;
  }
  return true;
}
function itemMatchesSearch(item) {
  if (!searchActive()) return true;
  if (item.type !== "transcript") return false;
  const terms = freeSearchTerms().map(lower);
  const text = lower(item.text || "");
  if (terms.some((t) => !text.includes(t))) return false;
  const fromChips = searchChips.filter((chip) => chip.op === "from");
  const mentionChips = searchChips.filter((chip) => chip.op === "mentions");
  if (fromChips.length && !fromChips.some((chip) => chipMatches(item, chip))) return false;
  if (mentionChips.length && !mentionChips.some((chip) => chipMatches(item, chip))) return false;
  return searchChips
    .filter((chip) => chip.op !== "from" && chip.op !== "mentions")
    .every((chip) => chipMatches(item, chip));
}
function knownSpeakers() {
  const byKey = {};
  const add = (userId, name, avatar, client, kind) => {
    const display = name || (userId ? unknownLabel(userId) : "Unknown");
    const byPerson = display && !/^Unknown\s/i.test(display);
    const key = byPerson ? (String(client || "").toLowerCase() + ":" + lower(display))
                         : String(userId || (client || "") + ":" + display).toLowerCase();
    if (!key) return;
    const prev = byKey[key] || {};
    const userIds = (prev.userIds || []).slice();
    if (userId && !userIds.some((id) => lower(id) === lower(userId))) userIds.push(userId);
    byKey[key] = {
      userId: prev.userId || userId || "",
      userIds,
      name: prev.name || display,
      avatar: avatar || prev.avatar || (userId ? emojiAvatar(userId) : DEFAULT_AV_GRAY),
      client: client || prev.client || "",
      kind: prev.kind && prev.kind !== kind ? "mixed" : (kind || prev.kind || ""),
    };
  };
  for (const client in rosters) {
    for (const m of rosters[client] || []) add(m.userId, m.name, m.avatar, client, m.stream ? "stream" : "voice");
  }
  for (const userId in sources) {
    const s = sources[userId] || {};
    add(userId, s.resolved ? s.name : unknownLabel(userId), s.resolved ? s.avatar : emojiAvatar(userId), s.client, s.kind);
  }
  return Object.values(byKey).sort((a, b) => lower(a.name).localeCompare(lower(b.name)));
}
function findKnownSpeaker(value) {
  const v = lower(value);
  return knownSpeakers().find((u) =>
    lower(u.name) === v || lower(u.userId) === v || (u.userIds || []).some((id) => lower(id) === v));
}
function knownChannelValue(value) {
  const v = lower(value);
  if (v === "stream" || v === "voice") return true;
  const labels = new Set();
  Object.keys(panels).forEach((k) => { labels.add(k); labels.add(lower(clientLabel(k))); });
  (clientList || []).forEach((c) => { labels.add(lower(c.exe)); labels.add(lower(clientLabel(c.exe))); });
  return labels.has(v);
}
function canChipOperator(op, value, atEnd, forceUser) {
  if (!SEARCH_OPS.has(op) || !value) return false;
  if (op === "has") return ["link", "code"].includes(lower(value));
  if (op === "before" || op === "after" || op === "during") return !!parseLooseDate(value);
  if (op === "from" || op === "mentions") return forceUser || !atEnd;
  if (op === "in") return !atEnd || knownChannelValue(value);
  return true;
}
function addSearchChip(op, value, user, quiet) {
  op = lower(op); value = stripQuotes(value);
  if (!canChipOperator(op, value, false, true)) return false;
  const chip = { op, value };
  if (user) {
    chip.userId = user.userId || "";
    chip.userIds = (user.userIds || (user.userId ? [user.userId] : [])).slice();
    chip.name = user.name || value;
    chip.avatar = user.avatar || "";
    chip.client = user.client || "";
  }
  searchChips.push(chip);
  renderSearchChips();
  if (!quiet) applySearch();
  return true;
}
function removeSearchChip(i) {
  searchChips.splice(i, 1);
  renderSearchChips();
  applySearch();
}
function chipLabel(chip) {
  if ((chip.op === "from" || chip.op === "mentions") && chip.name) return chip.name;
  return chip.value;
}
function updateSearchExpanded() {
  const wrap = $("search-wrap"), input = $("search-input"), pop = $("search-suggest"), clear = $("search-clear");
  if (!wrap || !input) return;
  const open = document.activeElement === input || searchActive() || (pop && pop.classList.contains("show"));
  wrap.classList.toggle("expanded", !!open);
  if (clear) clear.style.display = open ? "inline-flex" : "none";
}
function renderSearchChips() {
  const box = $("searchbox"), input = $("search-input"), clear = $("search-clear");
  if (!box || !input) return;
  box.querySelectorAll(".search-chip").forEach((c) => c.remove());
  searchChips.forEach((chip, i) => {
    const el = document.createElement("span"); el.className = "search-chip";
    const op = document.createElement("b"); op.textContent = chip.op + ":";
    el.appendChild(op);
    if (chip.op === "from" || chip.op === "mentions") {
      const img = document.createElement("img"); img.src = chip.avatar || (chip.userId ? emojiAvatar(chip.userId) : DEFAULT_AV_GRAY);
      img.onerror = () => { img.src = chip.userId ? emojiAvatar(chip.userId) : DEFAULT_AV_GRAY; };
      const nm = document.createElement("span"); nm.className = "sc-name"; nm.textContent = chipLabel(chip);
      el.append(img, nm);
    } else {
      const v = document.createElement("span"); v.className = "sc-name"; v.textContent = chip.value; el.appendChild(v);
    }
    const x = document.createElement("span"); x.className = "sc-x"; x.innerHTML = icon("x");
    x.onclick = (e) => { e.stopPropagation(); removeSearchChip(i); };
    el.appendChild(x);
    box.insertBefore(el, input);
  });
  updateSearchExpanded();
}
function activeSearchToken() {
  const input = $("search-input"); if (!input) return null;
  const pos = input.selectionStart == null ? input.value.length : input.selectionStart;
  if (pos !== input.value.length) return null;
  const before = input.value.slice(0, pos);
  const m = before.match(/(^|\s)(\S*)$/);
  if (!m) return null;
  const raw = m[2] || "";
  const start = before.length - raw.length;
  const colon = raw.indexOf(":");
  const op = colon >= 0 ? lower(raw.slice(0, colon)) : "";
  const query = colon >= 0 ? raw.slice(colon + 1) : raw;
  return { raw, op, query, hasColon: colon >= 0, start, end: pos };
}
function activeValueToken() {
  const tok = activeSearchToken();
  return tok && tok.hasColon && SEARCH_OPS.has(tok.op) ? tok : null;
}
function replaceActiveToken(text) {
  const input = $("search-input"), tok = activeSearchToken();
  if (!input || !tok) return;
  input.value = input.value.slice(0, tok.start) + text + input.value.slice(tok.end);
  input.selectionStart = input.selectionEnd = tok.start + text.length;
  input.focus();
}
function knownChannels() {
  const byValue = { voice: { value: "voice", label: "in:voice", meta: "kind", icon: "mic" },
                    stream: { value: "stream", label: "in:stream", meta: "kind", icon: "screen-share" } };
  Object.keys(panels).forEach((k) => {
    const label = clientLabel(k);
    byValue[lower(label)] = { value: label, label: "in:" + label, meta: k, icon: "hash" };
  });
  (clientList || []).forEach((c) => {
    const label = clientLabel(c.exe);
    byValue[lower(label)] = { value: label, label: "in:" + label, meta: c.exe, icon: "hash" };
  });
  return Object.values(byValue).sort((a, b) => lower(a.label).localeCompare(lower(b.label)));
}
function dateSuggestItems(op, q) {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const values = ["today", "yesterday", now.getFullYear() + "-" + mm + "-" + dd, mm + "/" + dd];
  return values.map((value) => ({ kind: "value", op, value, label: op + ":" + value, meta: "date", icon: "calendar" }))
    .filter((it) => !q || lower(it.value).includes(q) || lower(it.label).includes(q));
}
function suggestionItemsForToken(tok) {
  const q = lower(tok ? tok.query : "");
  if (!tok || !tok.hasColon) {
    const raw = lower(tok ? tok.raw : "");
    return SEARCH_OPERATORS
      .filter((it) => !raw || it.op.startsWith(raw) || (it.op + ":").startsWith(raw))
      .map((it) => ({ kind: "operator", op: it.op, label: it.op + ":", meta: it.meta, icon: it.icon }));
  }
  if (!SEARCH_OPS.has(tok.op)) return [];
  if (tok.op === "from" || tok.op === "mentions") {
    return knownSpeakers().filter((u) =>
      !q || lower(u.name).includes(q) || lower(u.userId).includes(q) || (u.userIds || []).some((id) => lower(id).includes(q)))
      .slice(0, 8).map((u) => ({ kind: "speaker", op: tok.op, user: u, label: u.name || "Unknown", icon: "user",
                                 meta: [clientLabel(u.client), u.userId].filter(Boolean).join(" - ") }));
  }
  if (tok.op === "has") {
    return [{ value: "link", icon: "link" }, { value: "code", icon: "code-2" }]
      .map((it) => ({ kind: "value", op: "has", value: it.value, label: "has:" + it.value, meta: "content", icon: it.icon }))
      .filter((it) => !q || it.value.startsWith(q));
  }
  if (tok.op === "in") {
    return knownChannels().filter((it) => !q || lower(it.value).includes(q) || lower(it.label).includes(q))
      .slice(0, 8).map((it) => Object.assign({ kind: "value", op: "in" }, it));
  }
  if (tok.op === "before" || tok.op === "after" || tok.op === "during") return dateSuggestItems(tok.op, q).slice(0, 8);
  return [];
}
function renderSearchSuggest() {
  const pop = $("search-suggest"), tok = activeSearchToken();
  if (!pop || !tok) return hideSearchSuggest();
  searchSuggestItems = suggestionItemsForToken(tok);
  searchSuggestIndex = Math.max(0, Math.min(searchSuggestIndex, searchSuggestItems.length - 1));
  pop.innerHTML = "";
  if (!searchSuggestItems.length) return hideSearchSuggest();
  searchSuggestItems.forEach((it, i) => {
    const row = document.createElement("div"); row.className = "ss-item" + (i === searchSuggestIndex ? " active" : "");
    if (it.kind === "speaker") {
      const img = document.createElement("img"); img.src = it.user.avatar || DEFAULT_AV_GRAY;
      img.onerror = () => { img.src = it.user.userId ? emojiAvatar(it.user.userId) : DEFAULT_AV_GRAY; };
      row.appendChild(img);
    } else {
      const badge = document.createElement("span"); badge.className = "ss-op"; badge.innerHTML = icon(it.icon || "hash");
      row.appendChild(badge);
    }
    const main = document.createElement("div"); main.className = "ss-main";
    const name = document.createElement("span"); name.className = "ss-name"; name.textContent = it.label;
    const handle = document.createElement("span"); handle.className = "ss-handle";
    handle.textContent = it.meta || "";
    main.append(name, handle);
    row.appendChild(main);
    row.onclick = (e) => { e.stopPropagation(); selectSearchSuggestion(it); };
    pop.appendChild(row);
  });
  pop.classList.add("show");
  updateSearchExpanded();
}
function hideSearchSuggest() {
  const pop = $("search-suggest"); if (pop) pop.classList.remove("show");
  searchSuggestItems = []; searchSuggestIndex = 0;
  updateSearchExpanded();
}
function selectSearchSuggestion(it) {
  if (!it) return;
  if (it.kind === "operator") {
    replaceActiveToken(it.op + ":");
    searchSuggestIndex = 0;
    renderSearchSuggest();
    return;
  }
  if (it.kind === "speaker") addSearchChip(it.op, it.user.name || it.user.userId, it.user);
  else addSearchChip(it.op, it.value);
  replaceActiveToken("");
  hideSearchSuggest();
}
function consumeSearchTokens(forceUser) {
  const input = $("search-input"); if (!input) return false;
  const raw = input.value;
  const re = /\S+/g;
  let m, last = 0, out = "", changed = false;
  while ((m = re.exec(raw)) !== null) {
    const token = m[0], start = m.index, end = start + token.length;
    const opm = token.match(/^([a-z]+):(.+)$/i);
    if (!opm) continue;
    const op = lower(opm[1]), value = stripQuotes(opm[2]);
    const atEnd = raw.slice(end).trim().length === 0;
    if (!canChipOperator(op, value, atEnd, !!forceUser)) continue;
    const user = (op === "from" || op === "mentions") ? findKnownSpeaker(value) : null;
    out += raw.slice(last, start);
    addSearchChip(op, value, user, true);
    changed = true;
    last = end;
  }
  if (!changed) return false;
  out += raw.slice(last);
  input.value = out.replace(/\s{2,}/g, " ").trimStart();
  renderSearchChips();
  applySearch();
  return true;
}
function applySearch() {
  renderSearchChips();
  renderAllPanels();
}
function initSearch() {
  const input = $("search-input"), clear = $("search-clear"), box = $("searchbox");
  if (!input || !clear || !box) return;
  const searchIc = $("search-ic");
  if (searchIc) searchIc.innerHTML = icon("search");
  clear.innerHTML = icon("x");
  box.addEventListener("click", () => { input.focus(); renderSearchSuggest(); });
  input.addEventListener("focus", renderSearchSuggest);
  input.addEventListener("blur", () => setTimeout(updateSearchExpanded, 120));
  input.addEventListener("input", () => {
    consumeSearchTokens(false);
    renderSearchSuggest();
    applySearch();
  });
  input.addEventListener("keydown", (e) => {
    const suggestOpen = $("search-suggest") && $("search-suggest").classList.contains("show");
    if (suggestOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      searchSuggestIndex = (searchSuggestIndex + (e.key === "ArrowDown" ? 1 : -1) + searchSuggestItems.length) % searchSuggestItems.length;
      renderSearchSuggest();
      return;
    }
    if (suggestOpen && (e.key === "Enter" || e.key === "Tab")) {
      e.preventDefault();
      if (searchSuggestItems[searchSuggestIndex]) selectSearchSuggestion(searchSuggestItems[searchSuggestIndex]);
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && activeValueToken()) {
      const tok = activeValueToken();
      if (tok.query) {
        e.preventDefault();
        const user = (tok.op === "from" || tok.op === "mentions") ? findKnownSpeaker(tok.query) : null;
        addSearchChip(tok.op, tok.query, user || null);
        replaceActiveToken("");
        hideSearchSuggest();
      }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      consumeSearchTokens(true);
      hideSearchSuggest();
    } else if (e.key === "Backspace" && !input.value && searchChips.length) {
      e.preventDefault();
      searchChips.pop();
      renderSearchChips();
      applySearch();
    } else if (e.key === "Escape") {
      hideSearchSuggest();
    }
  });
  clear.onclick = (e) => {
    e.stopPropagation();
    searchChips = [];
    input.value = "";
    renderSearchChips();
    applySearch();
    input.focus();
    renderSearchSuggest();
  };
  document.addEventListener("click", (e) => { if (!e.target.closest(".search-wrap")) hideSearchSuggest(); });
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
function flipPanel(p) {                     // per-card direction toggle
  p.newestTop = !p.newestTop;
  if (p.jump) p.jump.textContent = jumpLabel(p.newestTop);
  if (p.flipBtn) p.flipBtn.title = flipTitle(p.newestTop);
  p.pinned = true; p.lastAuto = Date.now();
  renderPanel(p, { forcePin: true });
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
  renderClients();
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
function overlayInfo(c) {
  const enabled = injectFor(c.exe);
  const es = engineStatus[c.exe] || {};
  const ov = es.overlay || {};
  if (!enabled) {
    if (engineRunning && ov.state === "attached") {
      return { dot: "warn", label: "detach pending", tip: "Overlay is still attached until you restart the overlay." };
    }
    return { dot: "off", label: "off", tip: "Overlay disabled for this client." };
  }
  if (!engineRunning) {
    return { dot: "off", label: "starts with engine", tip: "Start the engine to attach the overlay." };
  }
  if (ov.state === "attached") {
    return { dot: "good", label: "attached", tip: "Overlay is attached to this Discord client." };
  }
  if (ov.state === "attaching") {
    return { dot: "info", label: "attaching", tip: "Overlay injection is in progress." };
  }
  if (ov.state === "reloading") {
    return { dot: "info", label: "reloading", tip: "Overlay is being re-injected with the latest settings." };
  }
  if (ov.state === "failed") {
    return { dot: "bad", label: "failed", tip: ov.detail || "Overlay injection failed." };
  }
  if (ov.state === "disabled") {
    return { dot: "warn", label: "attach pending", tip: "Overlay is enabled here; restart the overlay to attach it." };
  }
  if (es.cdp || c.live) {
    return { dot: "info", label: "ready", tip: "Debug port is connected; the overlay will attach when the engine applies it." };
  }
  if (c.running) {
    return { dot: "warn", label: "waiting for port", tip: "Restart this client with its debug port before the overlay can attach." };
  }
  return { dot: "off", label: "waiting for client", tip: "Launch this Discord client before attaching the overlay." };
}
function renderOverlayClients() {
  const box = $("overlay_clients");
  if (!clientList.length) { box.innerHTML = '<div class="hint">No Discord clients detected yet.</div>'; return; }
  box.innerHTML = "";
  for (const c of clientList) {
    const info = overlayInfo(c);
    const row = document.createElement("div"); row.className = "toggrow overlayrow"; row.title = info.tip;
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = c.folder;
    const dot = document.createElement("span"); dot.className = "cdot " + info.dot;
    const st = document.createElement("span"); st.className = "st"; st.textContent = info.label;
    row.append(nm, makeSwitch(injectFor(c.exe), (on) => setInject(c.exe, on)), dot, st);
    box.appendChild(row);
  }
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
const PARAKEET_MODEL_DEFAULT = "parakeet-tdt-0.6b-v3-int8";
let lastWhisperLang = "";
const toHex = (s) => {
  s = String(s || "#f04747").trim();
  if (/^#[0-9a-fA-F]{3}$/.test(s)) s = "#" + s.slice(1).split("").map((c) => c + c).join("");
  return /^#[0-9a-fA-F]{6}$/.test(s) ? s.toLowerCase() : "#f04747";
};

function applyEngineConstraints(engine) {
  engine = engine === "parakeet" ? "parakeet" : "whisper";
  const isParakeet = engine === "parakeet";
  const lang = $("adv_lang");
  const dev = $("adv_device");
  if (lang && !lang.dataset.autoText) lang.dataset.autoText = lang.options[0].textContent;
  if ($("asr_engine")) $("asr_engine").value = engine;

  if (isParakeet) {
    if (lang && !lang.disabled) lastWhisperLang = lang.value || lastWhisperLang;
    if (lang) {
      lang.value = "";
      lang.options[0].textContent = "Auto (25 European languages)";
      lang.disabled = true;
    }
    if (dev && (dev.value === "hip" || dev.value === "vulkan")) dev.value = "auto";
  } else if (lang) {
    lang.disabled = false;
    lang.options[0].textContent = lang.dataset.autoText || "Auto-detect";
    if (lastWhisperLang) lang.value = lastWhisperLang;
  }

  for (const id of ["whisper_model_row", "beam_row", "compute_row"]) {
    const el = $(id);
    if (el) el.style.display = isParakeet ? "none" : "";
  }
  const pm = $("parakeet_model_row");
  if (pm) pm.style.display = isParakeet ? "" : "none";
  const sound = $("transcribe_sounds");
  if (sound) sound.disabled = isParakeet;

  if (dev) {
    Array.from(dev.options).forEach((opt) => {
      const blocked = isParakeet && (opt.value === "hip" || opt.value === "vulkan");
      opt.disabled = blocked;
      opt.title = blocked ? "AMD/Intel GPU acceleration is not available for Parakeet. Use Whisper for hip/vulkan, or Parakeet on CPU." : "";
    });
  }

  const note = $("engine_note");
  if (note) {
    note.textContent = isParakeet
      ? "Parakeet: fast multilingual speech recognition for 25 European languages, NVIDIA or CPU. Model downloads on first Start."
      : "";
    note.style.color = "var(--mut)";
  }
  refreshGpu();
}

function fillForm(c) {
  CFG = c;
  $("asr_engine").value = c.asr_engine || "whisper";
  $("whisper_model").value = c.whisper_model;
  $("parakeet_model").value = c.parakeet_model || PARAKEET_MODEL_DEFAULT;
  $("transcribe_sounds").checked = c.transcribe_sounds !== false;
  $("cap_screen").checked = (c.capture || {}).screenshare !== false;
  const g = c.gating || {};
  $("g_dbfs").value = g.min_rms_dbfs ?? -50;
  $("g_dbfs_v").textContent = $("g_dbfs").value;
  $("g_vad").checked = g.vad !== false;
  $("g_reqspeak").checked = g.require_speaking !== false;
  $("g_drop").value = (g.drop_phrases || []).join(", ");
  $("g_uncensor").checked = !!c.uncensor;
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
  $("o_subs").checked = o.show_subtitles !== false;
  $("o_log").checked = o.show_log !== false;
  $("o_status").checked = o.show_status !== false;
  $("o_shrink").checked = o.shrink_quiet_subtitles === true;
  $("o_merge").checked = o.merge_subtitles !== false;
  $("o_logw").value = o.log_width ?? 360;
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
  lastWhisperLang = c.language || "";
  applyEngineConstraints(c.asr_engine || "whisper");
  const s = c.self_transcribe || {};
  $("self_en").checked = !!s.enabled;
  $("self_unmute").checked = s.only_when_unmuted !== false;
  $("self_vad").checked = s.require_discord_speaking !== false;
  $("self_ns").checked = s.noise_suppression !== false;
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
    asr_engine: $("asr_engine").value,
    whisper_model: $("whisper_model").value,
    parakeet_model: $("parakeet_model").value || PARAKEET_MODEL_DEFAULT,
    transcribe_sounds: $("transcribe_sounds").checked,
    voice_events: $("ui_events").checked,
    uncensor: $("g_uncensor").checked,
    capture: Object.assign({}, CFG.capture, { screenshare: $("cap_screen").checked }),
    language: $("asr_engine").value === "parakeet" ? "" : $("adv_lang").value.trim(),
    beam_size: parseInt($("adv_beam").value, 10) || 1,
    device: $("asr_engine").value === "parakeet" && ($("adv_device").value === "hip" || $("adv_device").value === "vulkan")
      ? "auto" : $("adv_device").value,
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
      show_subtitles: $("o_subs").checked,
      show_log: $("o_log").checked,
      show_status: $("o_status").checked,
      shrink_quiet_subtitles: $("o_shrink").checked,
      merge_subtitles: $("o_merge").checked,
      log_width: parseInt($("o_logw").value, 10) || 360,
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
      noise_suppression: $("self_ns").checked,
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
  const sug = $("kw-suggest");
  if (sug) sug.onclick = () => {
    if (!selfNames.length) { toast("Join a voice call so we can read your name", false); return; }
    const s = keywordSuggestions(selfNames);
    if (!s.length) { toast("Your names are already in the list", false); return; }
    openKeywordSetup(s, false);
  };
}

// ---------- first-run "alert on your name" keyword setup ----------
// Pre-fill the user's own Discord names (server nick, real display name, username) as alert keywords,
// plus a first-name split of any multi-word name (people often say just the first name). Deduped and
// minus anything already in the list.
let selfNames = [];           // our own names, learned from the engine's selfIdentity broadcast
let kwPromptShown = false;    // only auto-open the first-run popup once per session
function keywordSuggestions(names) {
  const out = [], seen = new Set();
  const add = (s) => {
    s = String(s || "").trim();
    if (s.length < 2) return;
    const key = s.toLowerCase();
    if (seen.has(key)) return;
    if (kwList.some((k) => k.toLowerCase() === key)) return;   // already a keyword
    seen.add(key); out.push(s);
  };
  for (const n of names) {
    add(n);
    const first = String(n || "").trim().split(/\s+/)[0];      // first-name split for multi-word names
    if (first && first.toLowerCase() !== String(n).trim().toLowerCase()) add(first);
  }
  return out;
}
function onSelfIdentity(names) {
  if (!names || !names.length) return;
  // merge new names in (multiple clients may report); keep order, dedup case-insensitively
  const have = new Set(selfNames.map((n) => n.toLowerCase()));
  for (const n of names) { const k = String(n || "").trim(); if (k && !have.has(k.toLowerCase())) { have.add(k.toLowerCase()); selfNames.push(k); } }
  if (!kwPromptShown && CFG && CFG.setup_completed && !CFG.keyword_onboarded) {
    const s = keywordSuggestions(selfNames);
    if (s.length) { kwPromptShown = true; openKeywordSetup(s, true); }
  }
}
function markKeywordOnboarded() { if (CFG && !CFG.keyword_onboarded) { CFG.keyword_onboarded = true; scheduleSave(); } }
function closeKeywordSetup() { const e = $("kwsetup"); if (e) e.remove(); }
function openKeywordSetup(suggestions, firstRun) {
  closeKeywordSetup();
  let names = suggestions.slice();
  const bg = document.createElement("div"); bg.className = "modal-bg"; bg.id = "kwsetup";
  const card = document.createElement("div"); card.className = "modal";
  const h = document.createElement("h3"); h.textContent = "Alert on your name";
  const p = document.createElement("p");
  p.textContent = "These are your Discord names. Add them as alert keywords to get pinged when someone says them in voice. Remove any you don't want.";
  const chips = document.createElement("div"); chips.className = "kwchips";
  function renderChips() {
    chips.innerHTML = "";
    if (!names.length) { const e = document.createElement("span"); e.className = "hint"; e.textContent = "No names left to add."; chips.appendChild(e); return; }
    names.forEach((n, i) => {
      const tag = document.createElement("span"); tag.className = "pill-tag";
      const b = document.createElement("b"); b.textContent = n;
      const x = document.createElement("span"); x.className = "pill-x"; x.textContent = "×";
      x.onclick = () => { names.splice(i, 1); renderChips(); };
      tag.append(b, x); chips.appendChild(tag);
    });
  }
  renderChips();
  const actions = document.createElement("div"); actions.className = "actions";
  const skip = document.createElement("button"); skip.className = "sec"; skip.textContent = firstRun ? "Not now" : "Cancel";
  skip.onclick = () => { if (firstRun) markKeywordOnboarded(); closeKeywordSetup(); };
  const add = document.createElement("button"); add.textContent = "Add these";
  add.onclick = () => {
    const n = names.length;
    names.forEach(addKw);
    if (firstRun) markKeywordOnboarded();
    closeKeywordSetup();
    if (n) toast(n + " keyword" + (n === 1 ? "" : "s") + " added ✓", false);
  };
  actions.append(skip, add);
  card.append(h, p, chips, actions);
  bg.appendChild(card);
  bg.addEventListener("click", (e) => { if (e.target === bg) { if (firstRun) markKeywordOnboarded(); closeKeywordSetup(); } });
  document.body.appendChild(bg);
}

// ---------- first-run setup wizard ----------
function closeSetupWizard() { const e = $("setupwiz"); if (e) e.remove(); }
let cachedHw = null;   // hardware does not change within a session: detect once, reuse on re-open

function openSetupWizard(manual) {
  closeSetupWizard();
  const firstRun = !(CFG && CFG.setup_completed);
  // Open instantly from cache/placeholders; the slow probes (a PowerShell GPU query and per-port CDP
  // checks) load in the background below and refresh only the step that uses them.
  let hw = cachedHw || { vendor: "cpu", name: "Detecting your hardware…", recommended_engine: "parakeet", recommended_device: "cpu" };
  let clients = clientList.slice();

  // What `device:auto` resolves to on this machine, in plain language. The wizard never lets a
  // beginner hand-pick a device, so HIP can never be chosen on an unsupported card here: auto routes
  // it, and Settings > Advanced keeps the manual override for power users who want it later.
  const resolvedDeviceLabel = (engine) => {
    const name = hw.name || "CPU";
    if (engine === "parakeet") {
      return hw.vendor === "nvidia" ? "your NVIDIA GPU" : "your CPU (not compatible with AMD or Intel GPUs)";
    }
    const dev = (hw.recommended_engine === "whisper" && hw.recommended_device)
      ? hw.recommended_device
      : (hw.vendor === "nvidia" ? "cuda" : hw.vulkan ? "vulkan" : "cpu");
    return dev === "cpu" ? "your CPU" : "your " + name;
  };
  const state = {
    step: 0,
    engine: firstRun ? "parakeet" : ((CFG && CFG.asr_engine) || "whisper"),  // NeMo is the recommended default
    device: (CFG && CFG.device) || "auto",   // wizard manages engine only; device stays auto unless overridden in Advanced
    language: (CFG && CFG.language) || "",
    parakeet_model: (CFG && CFG.parakeet_model) || PARAKEET_MODEL_DEFAULT,
    keywords: [],          // built when the keywords step opens, after the restart can detect names
    keywordsInit: false,
  };
  // Case-insensitive merge that keeps the existing order and never drops what's already saved.
  const mergeKw = (into, more) => {
    more.forEach((k) => { if (k && !into.some((x) => x.toLowerCase() === k.toLowerCase())) into.push(k); });
    return into;
  };
  if (state.engine === "parakeet" && (state.device === "hip" || state.device === "vulkan")) state.device = "auto";

  const bg = document.createElement("div"); bg.className = "modal-bg"; bg.id = "setupwiz";
  const card = document.createElement("div"); card.className = "modal wizard";
  const title = document.createElement("h3");
  const dots = document.createElement("div"); dots.className = "wiz-steps";
  const panel = document.createElement("div"); panel.className = "wiz-panel";
  const actions = document.createElement("div"); actions.className = "actions";
  const back = document.createElement("button"); back.className = "sec"; back.textContent = "Back";
  const cancel = document.createElement("button"); cancel.className = "sec"; cancel.textContent = firstRun ? "Skip" : "Cancel";
  const next = document.createElement("button");
  actions.append(cancel, back, next);
  card.append(title, dots, panel, actions);
  bg.appendChild(card);
  document.body.appendChild(bg);

  // Keywords come AFTER launch: detecting the user's names needs Discord restarted with the debug port.
  const steps = () => ["engine"].concat(state.engine === "whisper" ? ["language"] : [], ["launch", "keywords", "done"]);
  const stepTitle = (kind) => ({
    engine: "Choose engine",
    language: "Language",
    keywords: "Alert keywords",
    launch: "Launch Discord",
    done: "Done",
  })[kind] || "Setup";

  function renderEngine() {
    panel.innerHTML = "";
    const p = document.createElement("p");
    if (state.hwLoading) p.innerHTML = '<span class="wiz-spin"></span>Detecting your hardware…';
    else p.textContent = "Detected: " + (hw.name || "CPU") + ". Pick how transcription runs; runtimes and models download once on first Start.";
    panel.appendChild(p);

    const defs = [
      { id: "parakeet", name: "NeMo Parakeet", tag: "Recommended",
        points: ["Fastest, high accuracy", "25 European languages, auto-detected", "NVIDIA GPU or CPU"] },
      { id: "whisper", name: "Whisper", tag: "",
        points: ["99 languages, or pin one", "Sound captions like [laughs], (applause)", "Any GPU (NVIDIA, AMD, Intel) or CPU"] },
    ];
    const cards = document.createElement("div"); cards.className = "engine-cards";
    defs.forEach((d) => {
      const cardEl = document.createElement("div");
      cardEl.className = "engine-card" + (state.engine === d.id ? " sel" : "");
      const head = document.createElement("div"); head.className = "ec-head";
      const nm = document.createElement("b"); nm.textContent = d.name; head.appendChild(nm);
      if (d.tag) { const t = document.createElement("span"); t.className = "ec-tag"; t.textContent = d.tag; head.appendChild(t); }
      cardEl.appendChild(head);
      const ul = document.createElement("ul"); ul.className = "ec-points";
      d.points.forEach((pt) => { const li = document.createElement("li"); li.textContent = pt; ul.appendChild(li); });
      cardEl.appendChild(ul);
      const dev = document.createElement("div"); dev.className = "ec-dev";
      if (state.hwLoading) dev.innerHTML = '<span class="wiz-spin"></span>Checking…';
      else dev.textContent = "Will use " + resolvedDeviceLabel(d.id);
      cardEl.appendChild(dev);
      cardEl.onclick = () => {
        if (state.engine !== d.id) { state.engine = d.id; state.device = "auto"; }
        if (state.step >= steps().length) state.step = steps().length - 1;
        render();
      };
      cards.appendChild(cardEl);
    });
    panel.appendChild(cards);

    const hint = document.createElement("p"); hint.style.marginTop = "12px";
    hint.textContent = "Want a specific device like HIP or Vulkan? Set it later in Settings, Advanced.";
    panel.appendChild(hint);
  }

  function renderLanguage() {
    panel.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = "Whisper can auto-detect or pin one language for the call.";
    panel.appendChild(p);
    const row = document.createElement("div"); row.className = "row";
    row.innerHTML = '<label>Language</label><select id="wiz_lang"></select>';
    const sel = row.querySelector("select");
    Array.from($("adv_lang").options).forEach((src) => {
      const o = document.createElement("option");
      o.value = src.value;
      o.textContent = src.value === "" ? "Auto-detect" : src.textContent;
      sel.appendChild(o);
    });
    sel.value = state.language;
    sel.onchange = () => { state.language = sel.value; };
    panel.appendChild(row);
  }

  function renderKeywords() {
    // Build from what's already saved, then fold in any detected names. First visit seeds the list;
    // later visits only add newly detected names so the user's own edits are never undone.
    if (!state.keywordsInit) {
      const saved = mergeKw(((CFG && CFG.alerts && CFG.alerts.keywords) || []).slice(), kwList || []);
      state.keywords = mergeKw(saved, keywordSuggestions(selfNames));
      state.keywordsInit = true;
    } else {
      mergeKw(state.keywords, keywordSuggestions(selfNames));
    }

    panel.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = "Highlight and play a sound when these names or words are spoken. We added your saved ones and any names we detected. Add or remove any.";
    panel.appendChild(p);
    const chips = document.createElement("div"); chips.className = "kwchips";
    const draw = () => {
      chips.innerHTML = "";
      if (!state.keywords.length) {
        const e = document.createElement("span"); e.className = "hint"; e.textContent = "Nothing set yet. Add a name below, or skip this for now.";
        chips.appendChild(e); return;
      }
      state.keywords.forEach((n, i) => {
        const tag = document.createElement("span"); tag.className = "pill-tag";
        const b = document.createElement("b"); b.textContent = n;
        const x = document.createElement("span"); x.className = "pill-x"; x.textContent = "x";
        x.onclick = () => { state.keywords.splice(i, 1); draw(); };
        tag.append(b, x); chips.appendChild(tag);
      });
    };
    draw();
    panel.appendChild(chips);
    const row = document.createElement("div"); row.className = "row";
    row.innerHTML = '<label>Add keyword</label><input id="wiz_kw" type="text" placeholder="name or phrase" />';
    const input = row.querySelector("input");
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const v = input.value.trim();
        if (v && !state.keywords.some((k) => k.toLowerCase() === v.toLowerCase())) state.keywords.push(v);
        input.value = ""; draw();
      }
    });
    panel.appendChild(row);
    const redetect = document.createElement("button"); redetect.className = "sec"; redetect.textContent = "Detect my names again";
    redetect.style.alignSelf = "flex-start";
    redetect.onclick = () => { mergeKw(state.keywords, keywordSuggestions(selfNames)); draw(); };
    panel.appendChild(redetect);
  }

  function renderLaunch() {
    panel.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = "Restart Discord with the debug port so we can show speaker names and captions. This closes the current call.";
    panel.appendChild(p);
    const running = clients.filter((c) => c.running);   // only the clients actually open right now
    const box = document.createElement("div");
    if (!running.length) {
      box.innerHTML = state.clientsLoading
        ? '<div class="empty"><span class="wiz-spin"></span>Checking Discord…</div>'
        : '<div class="empty">Open Discord, then click Refresh.</div>';
    } else {
      running.forEach((c) => {
        const row = document.createElement("div"); row.className = "clientrow";
        const status = c.live
          ? '<span class="st ok">' + icon("check") + "Ready</span>"
          : '<span class="st">Needs a restart</span>';
        row.innerHTML = '<span class="cdot ' + (c.live ? "good" : "warn") + '"></span>'
          + '<span class="nm">' + c.folder + "</span>" + status;
        const btn = document.createElement("button"); btn.className = "sec";
        if (c.live) { btn.textContent = "Ready"; btn.disabled = true; }
        else { btn.textContent = "Restart with port"; }
        btn.onclick = async () => {
          btn.disabled = true; btn.textContent = "...";
          try { await API.ensure_client(c.folder, true); } catch (e) {}
          try { clients = await API.list_clients(); clientList = clients; renderLaunch(); renderClients(); } catch (e) {}
        };
        row.appendChild(btn);
        box.appendChild(row);
      });
    }
    panel.appendChild(box);
    const refresh = document.createElement("button"); refresh.className = "sec"; refresh.textContent = "Refresh";
    refresh.style.alignSelf = "flex-start";
    refresh.onclick = async () => {
      try { clients = await API.list_clients(); clientList = clients; renderLaunch(); renderClients(); } catch (e) {}
    };
    panel.appendChild(refresh);
  }

  function renderDone() {
    panel.innerHTML = "";
    const p = document.createElement("p");
    const where = resolvedDeviceLabel(state.engine);
    p.textContent = (state.engine === "parakeet")
      ? "Ready to use NeMo Parakeet on " + where + ". Language is auto-detected and sound captions stay off for this engine."
      : "Ready to use Whisper on " + where + ".";
    panel.appendChild(p);
  }

  async function finish() {
    const cfg = readForm();
    // The wizard list is already seeded from the saved set + detected names, so it is the final
    // set: this honors anything the user removed here while keeping everything they did not touch.
    const merged = (state.keywordsInit ? state.keywords : kwList).slice();
    cfg.asr_engine = state.engine;
    cfg.device = state.device;
    cfg.parakeet_model = state.parakeet_model || PARAKEET_MODEL_DEFAULT;
    cfg.language = state.engine === "parakeet" ? "" : state.language;
    cfg.keyword_onboarded = true;
    cfg.setup_completed = true;
    cfg.alerts = Object.assign({}, cfg.alerts, { keywords: merged });
    await API.save_config(cfg);
    CFG = cfg; kwList = merged;
    fillForm(CFG);
    pushLiveConfig(CFG);
    closeSetupWizard();
    if (engineRunning) markRestartNeeded();
    toast("Setup saved", false);
  }

  function render() {
    const kinds = steps();
    if (state.step >= kinds.length) state.step = kinds.length - 1;
    const kind = kinds[state.step];
    title.textContent = stepTitle(kind);
    dots.innerHTML = "";
    kinds.forEach((_, i) => {
      const d = document.createElement("span"); d.className = "wiz-dot" + (i <= state.step ? " active" : "");
      dots.appendChild(d);
    });
    if (kind === "engine") renderEngine();
    else if (kind === "language") renderLanguage();
    else if (kind === "keywords") renderKeywords();
    else if (kind === "launch") renderLaunch();
    else renderDone();
    back.disabled = state.step === 0;
    next.textContent = kind === "done" ? "Finish" : "Next";
  }

  back.onclick = () => { if (state.step > 0) { state.step -= 1; render(); } };
  cancel.onclick = () => { if (firstRun) { CFG.setup_completed = true; scheduleSave(); } closeSetupWizard(); };
  next.onclick = async () => {
    const kind = steps()[state.step];
    if (kind === "done") { await finish(); return; }
    state.step += 1; render();
  };
  bg.addEventListener("click", (e) => { if (e.target === bg && !firstRun) closeSetupWizard(); });

  // Background IO: never blocks opening, never locks the user. Each probe shows an indeterminate
  // spinner on its step, clears it when done, and refreshes only the step that uses it. The user can
  // skip ahead at any time. Hardware is cached, so re-opening is instant.
  state.hwLoading = !cachedHw;
  state.clientsLoading = true;
  const rerenderIf = (kinds) => { if ($("setupwiz") && kinds.indexOf(steps()[state.step]) >= 0) render(); };
  if (!cachedHw) {
    API.detect_hardware().then((info) => { if (info) { cachedHw = info; hw = info; } })
      .catch(() => {}).finally(() => { state.hwLoading = false; rerenderIf(["engine", "done"]); });
  }
  API.list_clients().then((list) => { if (Array.isArray(list)) { clients = list; clientList = list; } })
    .catch(() => {}).finally(() => { state.clientsLoading = false; rerenderIf(["launch"]); });

  render();
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
    if (e.target.id === "asr_engine") applyEngineConstraints(e.target.value);
    if (e.target.id === "adv_device") refreshGpu();
    if (e.target.id === "whisper_model") refreshModels();
    if (e.target.id === "ui_ts" || e.target.id === "ui_tsfmt") renderAllPanels();
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
  "transcribe_sounds",                                     // backend suppressors + text cleanup
  "self_en", "self_unmute", "self_vad", "self_ns", "self_device",  // own-voice (incl. live device switch)
  "g_dbfs", "g_vad", "g_reqspeak", "g_drop",               // silence gating
  "g_uncensor",                                            // restore self-bleeped profanity
  "adv_lang", "adv_beam",                                  // language + beam size
]);
// overlay-only settings: applied by re-injecting the overlay into Discord, NOT by an engine restart
const OVERLAY_FIELDS = new Set(["o_timeout", "o_max", "o_fade", "o_minop", "o_subs", "o_log", "o_status", "o_shrink", "o_merge", "o_logw", "o_logh", "a_sound"]);
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
      const fix = document.createElement("button"); fix.className = "sec glow"; fix.textContent = "Restart w/ port";
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
      if (c.running && !c.live) btn.classList.add("glow");   // CTA nudge: this is the one action that turns on names
      btn.onclick = async () => {
        btn.disabled = true; btn.textContent = "…";
        await API.ensure_client(c.folder, c.running && !c.live);
        setTimeout(refreshClients, 1500);
      };
    }
    row.appendChild(btn);
    box.appendChild(row);
  }
  renderOverlayClients();
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
    } else if (m.type === "selfIdentity") {
      onSelfIdentity(m.names || []);
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
  const p = { col, body, jump, cnt, client: key, label: client, rosterHead, rosterBottom, flipBtn: flip,
              newestTop: newestTop(), pinned: true, n: 0, items: [], active: {}, windowSize: HISTORY_WINDOW,
              lastAuto: 0, jt: null, collapseTimer: null };
  // recompute the face row when the column is resized (responsive header-vs-bottom + fit)
  try { p.ro = new ResizeObserver(() => renderColumnRoster(p)); p.ro.observe(col); } catch (e) {}
  flip.title = flipTitle(p.newestTop);
  flip.onclick = () => flipPanel(p);
  clr.onclick = () => {
    body.innerHTML = ""; p.items = []; p.active = {}; p.n = 0; p.windowSize = HISTORY_WINDOW;
    if (p.collapseTimer) { clearTimeout(p.collapseTimer); p.collapseTimer = null; }
    cnt.textContent = "0"; jump.style.display = "none";
  };
  body.addEventListener("scroll", () => {
    if (Date.now() - p.lastAuto < 130) return;            // ignore our own auto-scroll -> no flicker
    const atEnd = isAtLiveEdge(p);
    if (atEnd) {
      p.pinned = true; jump.style.display = "none"; if (p.jt) { clearTimeout(p.jt); p.jt = null; }
      maybeScheduleWindowCollapse(p);
    }
    else { p.pinned = false; if (!p.jt) p.jt = setTimeout(() => { if (!p.pinned) jump.style.display = "block"; p.jt = null; }, 180); }
  });
  jump.addEventListener("click", () => {
    p.pinned = true; jump.style.display = "none"; p.lastAuto = Date.now();
    body.scrollTop = p.newestTop ? 0 : body.scrollHeight;
    maybeScheduleWindowCollapse(p);
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
function isAtLiveEdge(p) {
  const b = p.body;
  return p.newestTop ? (b.scrollTop < 28) : (b.scrollTop + b.clientHeight >= b.scrollHeight - 28);
}
function isNearOldEdge(p) {
  const b = p.body;
  return p.newestTop ? (b.scrollTop + b.clientHeight >= b.scrollHeight - 60) : (b.scrollTop < 60);
}
function maybeScheduleWindowCollapse(p) {
  if (!p || p.windowSize <= HISTORY_WINDOW || searchActive()) {
    if (p && p.collapseTimer) { clearTimeout(p.collapseTimer); p.collapseTimer = null; }
    return;
  }
  if (!isAtLiveEdge(p) || isNearOldEdge(p)) {
    if (p.collapseTimer) { clearTimeout(p.collapseTimer); p.collapseTimer = null; }
    return;
  }
  if (p.collapseTimer) return;
  p.collapseTimer = setTimeout(() => {
    p.collapseTimer = null;
    if (p.windowSize <= HISTORY_WINDOW || searchActive() || !isAtLiveEdge(p) || isNearOldEdge(p)) return;
    p.windowSize = HISTORY_WINDOW;
    p.pinned = true;
    renderPanel(p, { forcePin: true });
  }, HISTORY_COLLAPSE_MS);
}
function allPanelItems(p) {
  return p.items.concat(Object.values(p.active)).sort((a, b) => a.seq - b.seq);
}
function filteredPanelItems(p) {
  return allPanelItems(p).filter(itemMatchesSearch);
}
function countTranscripts(items) {
  return items.filter((it) => it.type === "transcript" && it.text).length;
}
function updatePanelCount(p, filtered) {
  if (searchActive()) p.cnt.textContent = countTranscripts(filtered) + "/" + p.n;
  else p.cnt.textContent = String(p.n);
}
function loadOlderButton(p, hiddenCount) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "load-older";
  btn.textContent = "↑ Load 200 older";
  btn.title = hiddenCount + " older message" + (hiddenCount === 1 ? "" : "s") + " hidden";
  btn.onclick = (e) => {
    e.stopPropagation();
    p.windowSize += HISTORY_STEP;
    p.pinned = false;
    renderPanel(p, { afterLoad: true });
  };
  return btn;
}
function renderAllPanels() {
  Object.values(panels).forEach((p) => renderPanel(p));
}
function renderPanel(p, opts) {
  opts = opts || {};
  const body = p.body;
  const wasPinned = !!(opts.forcePin || p.pinned);
  const oldHeight = body.scrollHeight;
  const oldTop = body.scrollTop;
  const filtered = filteredPanelItems(p);
  const hidden = Math.max(0, filtered.length - p.windowSize);
  let visible = filtered.slice(hidden);
  if (p.newestTop) visible = visible.slice().reverse();

  body.innerHTML = "";
  const load = hidden > 0 ? loadOlderButton(p, hidden) : null;
  if (load && !p.newestTop) body.appendChild(load);
  if (!visible.length) {
    const empty = document.createElement("div"); empty.className = "empty";
    empty.textContent = searchActive() ? "No matches." : "No transcript yet.";
    body.appendChild(empty);
  } else {
    visible.forEach((item) => body.appendChild(item.type === "event" ? eventLine(item, p) : transcriptLine(item, p)));
  }
  if (load && p.newestTop) body.appendChild(load);
  updatePanelCount(p, filtered);

  if (opts.afterLoad) {
    p.lastAuto = Date.now();
    body.scrollTop = p.newestTop ? body.scrollHeight : 0;
    maybeScheduleWindowCollapse(p);
    return;
  }
  if (wasPinned) {
    p.pinned = true;
    pinScroll(p);
  } else {
    const delta = body.scrollHeight - oldHeight;
    body.scrollTop = p.newestTop ? oldTop + delta : oldTop;
  }
  maybeScheduleWindowCollapse(p);
}
function transcriptLine(item, p) {
  const line = document.createElement("div"); line.className = "tline";
  line.dataset.uid = item.userId;
  const img = document.createElement("img");
  img.onerror = () => { img.style.visibility = "hidden"; };
  img.onload = () => pinScroll(p);
  const body = document.createElement("div"); body.className = "body";
  body.innerHTML = `<div class="who"><span class="ts"></span><span class="nm"></span></div><div class="txt"></div>`;
  line.appendChild(img); line.appendChild(body);
  applySpeaker(line, item.userId, item.client);
  const showTs = CFG && CFG.ui && CFG.ui.show_timestamps;
  const tsEl = line.querySelector(".ts");
  tsEl.style.display = showTs ? "" : "none";
  if (showTs) tsEl.textContent = fmtTs(item.ts || Date.now());
  line.classList.toggle("interim", !item.isFinal);
  const txEl = line.querySelector(".txt");
  const text = item.text || (item.isFinal ? "" : "...");
  txEl.dataset.text = text;
  renderLineText(txEl, text);
  return line;
}
function eventLine(item, p) {
  const line = document.createElement("div"); line.className = "tevent";
  const meta = EVENT_ICON[item.event];
  const ico = document.createElement("span");
  ico.innerHTML = icon(meta ? meta[0] : "info");
  if (meta) ico.style.color = meta[1];
  const txt = document.createElement("span"); txt.className = "etxt";
  const showTs = CFG && CFG.ui && CFG.ui.show_timestamps;
  txt.innerHTML = (showTs ? '<span class="ts"></span> ' : "") + "<b></b> " + escapeHtml(EVENT_LABEL[item.event] || item.event);
  txt.querySelector("b").textContent = item.name || "someone";
  if (showTs) txt.querySelector(".ts").textContent = fmtTs(item.ts || Date.now());
  line.appendChild(ico);
  if (item.avatar) {
    const av = document.createElement("img"); av.src = item.avatar; av.alt = "";
    av.onerror = () => { av.style.visibility = "hidden"; };
    av.onload = () => pinScroll(p);
    line.appendChild(av);
  }
  line.appendChild(txt);
  return line;
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
    p.newestTop = top;
    if (p.jump) p.jump.textContent = jumpLabel(p.newestTop);
    if (p.flipBtn) p.flipBtn.title = flipTitle(p.newestTop);
    p.pinned = true; p.lastAuto = Date.now();
    renderPanel(p, { forcePin: true });
  });
}

function renderEvent(m) {
  if (CFG && CFG.voice_events === false) return;
  const p = panelFor(m.client);
  p.items.push({
    type: "event", seq: ++itemSeq, event: m.event, name: m.name, avatar: m.avatar,
    client: m.client, ts: m.ts || Date.now()
  });
  renderPanel(p);
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
  let item = p.active[m.userId];
  if (!item) {
    item = {
      type: "transcript", seq: ++itemSeq, userId: m.userId, name: m.name, avatar: m.avatar,
      text: "", isFinal: false, client: m.client, kind: m.kind || "voice", ts: m.ts || Date.now(),
      resolved: m.resolved, locked: m.locked
    };
    p.active[m.userId] = item;
  }
  item.name = m.name !== undefined ? m.name : item.name;
  item.avatar = m.avatar !== undefined ? m.avatar : item.avatar;
  item.text = m.text || (m.isFinal ? "" : "...");
  item.isFinal = !!m.isFinal;
  item.client = m.client || item.client;
  item.kind = m.kind || item.kind || "voice";
  item.ts = m.ts || item.ts || Date.now();
  item.resolved = m.resolved !== undefined ? m.resolved : item.resolved;
  item.locked = m.locked !== undefined ? m.locked : item.locked;
  if (m.isFinal) {
    delete p.active[m.userId];
    if (m.text) { p.items.push(item); p.n++; }
  }
  renderPanel(p);
}

function applyRename(m) {
  renderAllPanels();
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
  initSearch();
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
  $("setup_wizard").onclick = () => openSetupWizard(true);
  setInterval(refreshEngine, 3000);
  setInterval(renderSpeakers, 1500);
  document.addEventListener("click", closeAssign);
  window.addEventListener("resize", closeAssign);
  checkUpdate();
  let forceSetup = false;
  try { forceSetup = await API.setup_requested(); } catch (e) {}
  if (forceSetup || !CFG.setup_completed) setTimeout(() => openSetupWizard(!!forceSetup), 150);
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
  asr_engine: "**ASR engine.** Whisper is the broad default with 99 languages, sound events, and all device paths. Parakeet is faster for 25 European languages, auto language only, and uses NVIDIA or CPU.",
  whisper_model: "**Speech model.** Bigger = more accurate, slower, more VRAM.\n- `tiny`/`base` - fastest, rough\n- `small` - good balance (default)\n- `medium`/`large-v3` - best accuracy (needs a strong GPU)\n\nModels download once and are reused - switching back never re-downloads.",
  parakeet_model: "**Parakeet model.** Phase 1 ships the int8 v3 model: 25 European languages, auto-detect, downloaded on first Start.",
  adv_lang: "**Language.** `Auto-detect` lets Whisper guess per utterance. Pin a language to stop it switching mid-call and to speed things up slightly. Parakeet always uses auto language mode.",
  cap_screen: "**Transcribe stream audio.** Include Go Live / screenshare audio (game, music, video) in transcription. Off = only people's microphones. Applies live, no restart.",
  transcribe_sounds: "**Transcribe sound events.** Keep emitted non-speech captions like `[laughs]`, `(applause)`, or `♪ music ♪`. Off strips those markers and treats sound-only output like silence. Applies live.",
  self_en: "**Transcribe your own microphone** in addition to everyone else's audio. Uses your mic, gated by Discord's own per-client mute/VAD state below.",
  self_unmute: "Only capture your mic while you are **unmuted in Discord** for that client. Off = transcribe even when self-muted.",
  self_vad: "Only capture your mic when **Discord's voice activity** says you're speaking for that client - avoids transcribing background room noise.",
  self_ns: "**Noise suppression** for your mic. Discord cleans up what others hear, but our capture is raw - this denoises fan/keyboard/room noise before transcription. Applies live.",
  g_dbfs: "**Silence gate.** Audio quieter than this (in dBFS) is skipped before it ever reaches the model. Higher (e.g. -45) gates harder and kills phantom *\"Thank you.\"* on near-silence.",
  g_vad: "**Silero VAD** trims non-speech regions from each chunk before transcription - fewer hallucinations on noise.",
  g_reqspeak: "**End when not speaking.** Closes an utterance once Discord's per-user speaking indicator goes quiet (after a short grace), which stops screenshare/comfort-noise bleed from transcribing forever. If your speech is being split into too many lines, turn this off to segment purely by audio.",
  g_drop: "**Drop phrases** (comma-separated) that Whisper hallucinates on silence (e.g. `thank you, bye`). Dropped only when the audio is quiet or low-confidence.",
  g_uncensor: "**Uncensor profanity.** Restore swear words Whisper self-bleeps into masked text. The word list is configured in `config.json`. Applies live, no restart.",
  kw: "**Keyword alerts.** Words that get **highlighted** + a beep when spoken (e.g. your name). Editing these re-highlights the existing transcript live.",
  a_sound: "Play a short **beep** when a keyword is detected.",
  a_highlight: "**Highlight color** used to mark keyword hits in the transcript and overlay.",
  ui_events: { md: "Show **voice events** (join/leave, mute, deafen, camera, stream) in the transcript and overlay, with icons.",
    preview: '<div style="font:12px sans-serif;color:#949ba4">'
      + '<div style="display:flex;align-items:center;gap:7px;opacity:.78;margin:3px 0"><span style="color:#23a55a;font-weight:700">↪</span><b style="color:#c4c9d0">Elly</b> joined the channel</div>'
      + '<div style="display:flex;align-items:center;gap:7px;opacity:.78;margin:3px 0"><span style="color:#949ba4">✕</span><b style="color:#c4c9d0">Sam</b> muted</div>'
      + '<div style="display:flex;align-items:center;gap:7px;opacity:.78;margin:3px 0"><span style="color:#5865f2">▣</span><b style="color:#c4c9d0">Von</b> started streaming</div></div>' },
  ui_newtop: "**Newest on top.** Off = newest lines at the bottom (classic chat). On = newest pops in at the top.",
  ui_ts: "Show a **timestamp** on each transcript line.",
  ui_tsfmt: "Timestamp style: **clock** (`14:03:22`) or **relative** (`12s ago`).",
  o_subs: { md: "**Show subtitles.** The live caption bubbles at the bottom of the Discord window. Turn off to keep only the transcript log.",
    preview: '<div style="background:rgba(0,0,0,.82);border-radius:10px;padding:6px 10px;display:flex;align-items:center;gap:8px;font:13px/1.3 sans-serif;color:#fff">'
      + '<span style="width:18px;height:18px;border-radius:50%;background:#5865f2;flex:0 0 auto"></span>'
      + '<span><b style="color:#7aa2ff;margin-right:5px">Elly</b>can you hear me <span style="color:#9bb7ff;font-style:italic">(laughs)</span><span style="color:#9bb7ff;margin-left:2px">▍</span></span></div>' },
  o_log: { md: "**Show transcript log.** The scrollable transcript panel docked top-right in Discord. Turn off to keep only the subtitles.",
    preview: '<div style="background:rgba(24,25,28,.95);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:6px 8px;font:12px/1.4 sans-serif;color:#e3e5e8;width:210px">'
      + '<div style="font-weight:600;font-size:11px;color:#b5bac1;margin-bottom:4px">Transcript</div>'
      + '<div style="display:flex;gap:6px;margin:3px 0;align-items:flex-start"><span style="width:14px;height:14px;border-radius:50%;background:#23a55a;flex:0 0 auto;margin-top:1px"></span><span><b style="color:#7aa2ff">Elly:</b> sounds good to me</span></div>'
      + '<div style="display:flex;gap:6px;margin:3px 0;align-items:flex-start"><span style="width:14px;height:14px;border-radius:50%;background:#f0b232;flex:0 0 auto;margin-top:1px"></span><span><b style="color:#ffcf7a">Sam:</b> let\'s ship it</span></div></div>' },
  o_status: { md: "**Show status pill.** The small \"Listening · N speaking\" connection indicator.",
    preview: '<div style="display:inline-flex;align-items:center;gap:6px;background:rgba(24,25,28,.92);border:1px solid rgba(255,255,255,.1);border-radius:13px;padding:4px 11px;font:11px sans-serif;color:#e3e5e8">'
      + '<span style="width:8px;height:8px;border-radius:50%;background:#23a55a;flex:0 0 auto"></span>Listening · 2 speaking</div>' },
  o_shrink: "**Shrink quiet subtitles.** Older subtitle blocks shrink after one second without new words; the current bottom subtitle stays full size.",
  o_merge: "**Merge consecutive subtitles.** When the same speaker starts a new sentence right after the last one, keep it in the same subtitle bubble instead of dropping the old one, so context isn't cut off mid-thought. Short lines merge readily; longer ones merge only within a brief gap. The transcript log still keeps each sentence as its own line.",
  o_logw: "Width of the in-Discord transcript log panel, in pixels (also drag-resizable by the panel's bottom-right edge).",
  o_logh: "Height of the in-Discord transcript log panel, in pixels (also drag-resizable).",
  o_timeout: "How long a subtitle stays on screen **after speech stops**, in milliseconds.",
  o_max: "Maximum number of subtitle blocks shown on the overlay at once.",
  o_fade: "Start fading older subtitles once this many are stacked.",
  o_minop: "Lowest opacity a faded subtitle reaches (0–1).",
  adv_beam: "**Beam size.** `1` = greedy & fastest. Higher = more accurate but slower.",
  adv_device: "**auto** picks the best for your GPU. **cuda** = NVIDIA. **hip** = AMD (ROCm); **vulkan** = any AMD/Intel GPU (the reliable AMD fallback if hip won't load). **cpu** works anywhere. Parakeet only supports auto, cuda, and cpu.",
  adv_compute: "Numeric precision. `float16` is best on GPU; `int8`/`int8_float16` use less memory; `float32` is CPU-friendly.",
  adv_relay: "Local WebSocket port the overlay connects to. Change only if `8765` clashes with something.",
};
let helpPop = null;
function closeHelp() { if (helpPop) { helpPop.remove(); helpPop = null; } }
function openHelp(anchor, help) {
  closeHelp();
  const md = typeof help === "string" ? help : help.md;
  const preview = typeof help === "object" && help ? help.preview : null;
  const pop = document.createElement("div"); pop.className = "help-pop";
  pop.innerHTML = mdToHtml(md) + (preview ? '<div class="help-preview">' + preview + "</div>" : "");
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
