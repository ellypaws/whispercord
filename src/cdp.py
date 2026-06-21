"""Minimal synchronous Chrome DevTools Protocol client (Python, no bun/BD needed at runtime).
Connects to a Discord client's --remote-debugging-port, attaches to the renderer page, evaluates JS.
Used to resolve who is speaking + user names/avatars for src->user binding.

Resolution is two-tier:
  1. PRIMARY: Discord's own Flux stores via webpack getStore / BetterDiscord's BdApi.Webpack.
     This is the ground truth (accurate speaking, works regardless of which channel is on screen)
     and is used whenever the stores are reachable (e.g. PTB w/ BetterDiscord).
  2. FALLBACK: React-fiber + DOM scraping of the rendered voice panel. On current builds the
     post-load webpack require is an incomplete runtime (only ~100 modules cached) so getStore
     fails on stable/Canary; there we read the voice panel's fibers (user/voiceState roster) and
     the DOM speaking ring instead. The fiber `speaking` prop is NOT trustworthy (self-only on
     Canary, stuck-true for several users on PTB), so speaking is taken from the DOM ring class."""
import json, time, urllib.request
from websockets.sync.client import connect


class CDP:
    def __init__(self, port=9223, http_timeout=1.5, open_timeout=5, command_timeout=10.0):
        self.port = port
        self.command_timeout = command_timeout
        ver = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=http_timeout))
        self.ws = connect(ver["webSocketDebuggerUrl"], max_size=None, open_timeout=open_timeout)
        self._id = 0
        infos = self._cmd("Target.getTargets")["result"]["targetInfos"]
        page = self._pick_page(infos)
        self.url = page.get("url")
        self.session = self._cmd("Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})["result"]["sessionId"]

    @staticmethod
    def _pick_page(infos):
        """Pick the MAIN app renderer. Discord also exposes popout/overlay/splash page targets;
        attaching to one of those means no voice panel and nothing resolves, so prefer /channels/
        and explicitly deprioritise /popout and /overlay."""
        pages = [t for t in infos if t["type"] == "page" and "discord" in t["url"].lower()]
        if not pages:
            raise RuntimeError("no Discord renderer page target found")
        def score(t):
            u = t["url"].lower()
            if "/popout" in u or "/overlay" in u:
                return -1
            return 2 if "/channels/" in u else 1
        return max(pages, key=score)

    def _cmd(self, method, params=None, sid=None, timeout=None):
        timeout = self.command_timeout if timeout is None else timeout
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))
        # Bound the wait: a wedged renderer (or a flood of unrelated events) must not block
        # forever — raise so the caller drops + reconnects this CDP connection.
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("CDP timeout waiting for %s" % method)
            d = json.loads(self.ws.recv(timeout=remaining))
            if d.get("id") == self._id:
                if d.get("error"):
                    raise RuntimeError("CDP %s error: %s" % (method, d["error"]))
                return d

    def evaluate(self, expr):
        r = self._cmd("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True},
                      self.session)
        res = r.get("result", {})
        if res.get("exceptionDetails"):
            return None
        return res.get("result", {}).get("value")

    def close(self):
        try: self.ws.close()
        except Exception: pass


# --- renderer expressions: webpack/BD stores (primary) with React-fiber/DOM fallback (secondary) ---
_BOOT = r"""
window.__vtr = (() => {
  const avatarUrl = (user, member, guildId) => {
    try {
      if (member && member.avatar && guildId)
        return "https://cdn.discordapp.com/guilds/" + guildId + "/users/" + user.id + "/avatars/" + member.avatar + ".png?size=64";
      if (user && user.avatar)
        return "https://cdn.discordapp.com/avatars/" + user.id + "/" + user.avatar + ".png?size=64";
    } catch (e) {}
    return "https://cdn.discordapp.com/embed/avatars/0.png";
  };

  // ======== PRIMARY: Discord Flux stores (webpack getStore / BdApi) ========
  const getReq = () => {
    if (window.__vtReq) return window.__vtReq;
    try {
      const chunk = (window.webpackChunkdiscord_app = window.webpackChunkdiscord_app || []);
      chunk.push([[Symbol("vt")], {}, (r) => { window.__vtReq = r; }]);
    } catch (e) {}
    return window.__vtReq;
  };
  const getStore = (name) => {
    if (window.BdApi && BdApi.Webpack && BdApi.Webpack.getStore) {
      try { const s = BdApi.Webpack.getStore(name); if (s) return s; } catch (e) {}
    }
    const req = getReq(); const c = req && req.c; if (!c) return null;
    for (const k in c) {
      let e; try { e = c[k] && c[k].exports; } catch (er) { continue; }
      if (!e) continue;
      try { if (e.getName && e.getName() === name) return e; } catch (er) {}
      try { if (e.default && e.default.getName && e.default.getName() === name) return e.default; } catch (er) {}
    }
    return null;
  };
  const stores = () => ({
    US: getStore("UserStore"), VSS: getStore("VoiceStateStore"), RCS: getStore("RTCConnectionStore"),
    SS: getStore("SpeakingStore"), GMS: getStore("GuildMemberStore"), CS: getStore("ChannelStore"),
    MES: getStore("MediaEngineStore"),
  });
  // each returns null when the stores aren't reachable, so the caller can fall back to the DOM
  const wpSpeaking = () => {
    const s = stores(); if (!s.SS || !s.VSS || !s.RCS || !s.US) return null;
    try {
      const me = s.US.getCurrentUser && s.US.getCurrentUser(); const meId = me && me.id;
      const ch = s.RCS.getChannelId && s.RCS.getChannelId(); if (!ch) return [];
      const st = (s.VSS.getVoiceStatesForChannel && s.VSS.getVoiceStatesForChannel(ch)) || {};
      return Object.keys(st).filter((uid) => uid !== meId && s.SS.isSpeaking && s.SS.isSpeaking(uid));
    } catch (e) { return null; }
  };
  const wpVoiceStates = () => {
    const s = stores(); if (!s.RCS || !s.VSS) return null;
    try {
      const ch = s.RCS.getChannelId && s.RCS.getChannelId(); if (!ch) return null;
      const st = (s.VSS.getVoiceStatesForChannel && s.VSS.getVoiceStatesForChannel(ch)) || {};
      const out = {};
      for (const uid in st) {
        const v = st[uid] || {};
        out[uid] = { selfMute: !!v.selfMute, selfDeaf: !!v.selfDeaf, mute: !!(v.mute || v.selfMute),
                     deaf: !!(v.deaf || v.selfDeaf), video: !!v.selfVideo, stream: !!v.selfStream, suppress: !!v.suppress };
      }
      return out;
    } catch (e) { return null; }
  };
  const wpSelf = () => {
    const s = stores(); if (!s.US) return null;
    try {
      const me = s.US.getCurrentUser && s.US.getCurrentUser(); if (!me) return null;
      const ch = s.RCS && s.RCS.getChannelId && s.RCS.getChannelId();   // THIS client's voice channel
      let muted = false, deaf = false, speaking = false;
      // Prefer this renderer's local media-engine state for self mute/deaf. VoiceStateStore can be
      // shared/stale for the account across clients, while MediaEngineStore reflects THIS client.
      let vs = null;
      try { if (ch && s.VSS && s.VSS.getVoiceStatesForChannel) vs = (s.VSS.getVoiceStatesForChannel(ch) || {})[me.id] || null; } catch (e) {}
      let mediaMute = null, mediaDeaf = null;
      try { if (s.MES && s.MES.isSelfMute) mediaMute = !!s.MES.isSelfMute(); } catch (e) {}
      try { if (s.MES && s.MES.isSelfDeaf) mediaDeaf = !!s.MES.isSelfDeaf(); } catch (e) {}
      const serverMute = !!(vs && (vs.mute || vs.suppress));
      const serverDeaf = !!(vs && vs.deaf);
      muted = (mediaMute !== null) ? (mediaMute || serverMute) : !!(vs && (vs.selfMute || vs.mute || vs.suppress));
      deaf = (mediaDeaf !== null) ? (mediaDeaf || serverDeaf) : !!(vs && (vs.selfDeaf || vs.deaf));
      let speakingReliable = false;
      try {
        if (s.SS && s.SS.isSpeaking) {
          speaking = !!s.SS.isSpeaking(me.id);
          speakingReliable = true;
        }
      } catch (e) {}
      let guildId = null, nick = null;       // our own server nickname, for keyword suggestions
      try {
        const chO = ch && s.CS && s.CS.getChannel && s.CS.getChannel(ch);
        guildId = chO && chO.guild_id;
        if (guildId && s.GMS && s.GMS.getMember) { const m = s.GMS.getMember(guildId, me.id); if (m) nick = m.nick; }
      } catch (e) {}
      return { selfId: me.id, channelId: ch || null, inCall: !!ch, muted: muted, deaf: deaf, speaking: speaking,
               reliable: true, muteReliable: true, speakingReliable: speakingReliable,
               username: me.username || null, globalName: me.globalName || null, nick: nick || null };
    } catch (e) { return null; }
  };
  const wpUser = (uid) => {
    const s = stores(); if (!s.US) return null;
    try {
      const u = s.US.getUser && s.US.getUser(uid); if (!u) return null;
      let guildId = null, nick = null, gAvatar = null;
      try {
        const chId = s.RCS && s.RCS.getChannelId && s.RCS.getChannelId();
        const ch = chId && s.CS && s.CS.getChannel && s.CS.getChannel(chId);
        guildId = ch && ch.guild_id;
        if (guildId && s.GMS && s.GMS.getMember) { const m = s.GMS.getMember(guildId, uid); if (m) { nick = m.nick; gAvatar = m.avatar; } }
      } catch (e) {}
      return { userId: u.id, name: nick || u.globalName || u.username, avatar: avatarUrl(u, { avatar: gAvatar }, guildId) };
    } catch (e) { return null; }
  };

  // ======== SSRC -> user map (native per-stream binding, the reliable path) ========
  // Discord exposes each connection's remote audio ssrcs as a plain { userId: ssrc } table on
  // conn._connection.remoteAudioSSRCs. The "default" voice connection carries everyone's MIC; each
  // watched Go Live opens a SEPARATE connection (StreamRTCConnectionStore, context "stream") whose
  // remote audio ssrc is that streamer's SCREENSHARE audio. So the connection a ssrc arrives on
  // deterministically tells mic ('voice') from screenshare ('stream') — no speaking-correlation
  // guessing, no run-length heuristics, no per-build offset hunting.
  const looksSsrc = (n) => typeof n === "number" && n > 0 && n < 4294967296 && Number.isInteger(n);
  const harvestRemote = (conn, kind, out, audio) => {
    if (!conn || !conn._connection) return;
    const m = conn._connection.remoteAudioSSRCs;            // { userId(str): ssrc(num), 0 = none }
    if (!m || typeof m !== "object") return;
    try {
      for (const uid in m) {
        const ssrc = m[uid];
        if (looksSsrc(ssrc) && uid && uid !== "undefined") { out[ssrc] = { userId: String(uid), kind: kind }; audio.push(ssrc); }
      }
    } catch (e) {}
  };
  const wpSsrcMap = () => {
    const s = stores(); if (!s.RCS) return null;
    try {
      const out = {}, audio = [];
      // mic audio of everyone in the channel (the default voice connection)
      let voice = null;
      try { voice = s.RCS.getRTCConnection && s.RCS.getRTCConnection(); } catch (e) {}
      harvestRemote(voice, "voice", out, audio);
      // screenshare audio of every watched Go Live (one connection per stream); harvested after
      // voice so a streamer's screenshare ssrc wins as 'stream' on the off chance of a collision.
      const SRCS = getStore("StreamRTCConnectionStore");
      if (SRCS) {
        const seen = [];
        const add = (cn) => { if (cn && seen.indexOf(cn) < 0) { seen.push(cn); harvestRemote(cn, "stream", out, audio); } };
        try {
          const cs = SRCS.getRTCConnections && SRCS.getRTCConnections();
          if (cs) { if (typeof cs.forEach === "function") cs.forEach(add); else Object.values(cs).forEach(add); }
        } catch (e) {}
        try {
          const keys = SRCS.getAllActiveStreamKeys && SRCS.getAllActiveStreamKeys();
          if (keys) for (const k of keys) { try { add(SRCS.getRTCConnection(k)); } catch (e) {} }
        } catch (e) {}
      }
      return { map: out, audio: audio };
    } catch (e) { return null; }
  };

  // ======== FALLBACK: React fiber + DOM scrape of the voice panel ========
  const fiberOf = (el) => {
    for (const k in el) { if (k[0] === "_" && (k.indexOf("__reactFiber$") === 0 || k.indexOf("__reactInternalInstance$") === 0)) return el[k]; }
    return null;
  };
  let _r = null, _rt = 0;
  const roster = () => {
    const now = Date.now();
    if (_r && now - _rt < 150) return _r;
    const out = {};
    // (a) container fiber holds props.voiceStates -> complete per-channel roster + mute/deaf
    const conts = document.querySelectorAll('[class*="voiceUsers"],[class*="voiceUser"],[class*="userList"]');
    for (const t of conts) {
      let f = fiberOf(t), hops = 0;
      while (f && hops < 30) {
        const p = f.memoizedProps;
        if (p && p.voiceStates && typeof p.voiceStates === "object" && !Array.isArray(p.voiceStates)) {
          const guildId = (p.channel && p.channel.guild_id) || null;
          for (const uid in p.voiceStates) {
            const e = p.voiceStates[uid]; if (!e) continue;
            const u = e.user; if (!u || !u.id) continue;
            const vs = e.voiceState || {};
            const nm = e.nick || (e.member && e.member.nick) || u.globalName || u.username;
            const o = out[u.id] || (out[u.id] = { uid: u.id });
            o.name = nm; o.username = u.username;
            o.avatar = avatarUrl(u, e.member, guildId);
            o.selfMute = !!vs.selfMute; o.selfDeaf = !!vs.selfDeaf;
            o.mute = !!(vs.mute || vs.selfMute); o.deaf = !!(vs.deaf || vs.selfDeaf);
            o.video = !!vs.selfVideo; o.stream = !!vs.selfStream; o.suppress = !!vs.suppress;
          }
          break;
        }
        f = f.return; hops++;
      }
    }
    // (b) per-tile speaking state from the DOM speaking ring / usernameSpeaking class. The fiber
    //     `speaking` prop is unreliable for remote users (stuck-true on some PTB builds), so remote
    //     speaking trusts the ring. Keep the fiber value separately because self speaking can be
    //     exposed there before/without the same visible ring.
    document.querySelectorAll('[class*="voiceUser"]').forEach((t) => {
      let f = fiberOf(t), hops = 0, u = null, nick = null, fsp = false;
      while (f && hops < 6) {
        const p = f.memoizedProps;
        if (p && p.user && p.user.id) { u = p.user; nick = p.nick; fsp = p.speaking === true; break; }
        f = f.return; hops++;
      }
      if (!u) return;
      const o = out[u.id] || (out[u.id] = { uid: u.id });
      if (o.name === undefined) o.name = nick || u.nick || u.globalName || u.username;
      if (o.username === undefined) o.username = u.username;
      if (o.avatar === undefined) o.avatar = avatarUrl(u, null, null);
      let dom = false;
      try {
        const speakingSel = '[class*="usernameSpeaking" i],[class*="avatarSpeaking" i],[class*="speaking" i]';
        dom = t.matches(speakingSel) || !!t.querySelector(speakingSel);
      } catch (e) {}
      o.domSpeaking = (o.domSpeaking === true) || dom;
      o.speaking = o.domSpeaking;
      o.fiberSpeaking = (o.fiberSpeaking === true) || fsp;
    });
    _r = out; _rt = now;
    return out;
  };
  const avatarScrape = (uid) => {
    const imgs = document.querySelectorAll('img[src*="/avatars/' + uid + '/"]');
    for (const img of imgs) {
      const alt = img.getAttribute("alt");
      if (alt && alt.trim()) return { userId: uid, name: alt, avatar: (img.src || "").replace(/\?.*$/, "") + "?size=64" };
    }
    return null;
  };
  const domSelf = () => {
    let selfId = null;
    const re = /\/avatars\/(\d{15,21})\//;
    const accImg = document.querySelector(
      '[class*="accountProfile"] img[src*="/avatars/"], [class*="avatarWrapper"] img[src*="/avatars/"], section[class*="panel"] img[src*="/avatars/"]');
    if (accImg) { const m = (accImg.src || "").match(re); if (m) selfId = m[1]; }
    let muted = false, deaf = false, inCall = false;
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const sw = Array.from(document.querySelectorAll('button[role="switch"][aria-label]')).filter(vis);
    const muteSw = sw.find((b) => /^(un)?mute$/i.test(b.getAttribute("aria-label") || ""));
    const deafSw = sw.find((b) => /^((un)?deafen)$/i.test(b.getAttribute("aria-label") || ""));
    if (muteSw) muted = muteSw.getAttribute("aria-checked") === "true" || /^unmute$/i.test(muteSw.getAttribute("aria-label") || "");
    if (deafSw) deaf = deafSw.getAttribute("aria-checked") === "true" || /^undeafen$/i.test(deafSw.getAttribute("aria-label") || "");
    if (!muteSw || !deafSw) {
      document.querySelectorAll('button[aria-label],[role="button"][aria-label]').forEach((b) => {
        if (!vis(b)) return;
        const a = (b.getAttribute("aria-label") || "");
        if (!muteSw && /^unmute$/i.test(a)) muted = true;
        if (!deafSw && /^undeafen$/i.test(a)) deaf = true;
      });
    }
    document.querySelectorAll('button[aria-label],[role="button"][aria-label]').forEach((b) => {
      if (vis(b) && /^disconnect$/i.test(b.getAttribute("aria-label") || "")) inCall = true;
    });
    let speaking = false;
    let speakingReliable = false;
    if (selfId) {
      const r = roster();
      const me = r[selfId];
      if (me) {
        speaking = !!(me.domSpeaking || me.fiberSpeaking);
        speakingReliable = true;
      }
    }
    // reliable:false — scraped from the DOM, only valid when this client's call panel is on screen;
    // the own-voice gate fails closed on an unreliable read so a muted/background client won't leak.
    return { selfId: selfId, channelId: inCall ? 1 : null, inCall: inCall, muted: muted, deaf: deaf, speaking: speaking,
             reliable: false, muteReliable: !!muteSw, speakingReliable: speakingReliable };
  };
  // Reliable, webpack-free self NAMES from the bottom-left account panel (anchored off the User
  // Settings gear): it always shows the global display name; the @username sits in its hover element.
  // Used to backfill self() when the Flux stores are unreachable (the common case on current builds).
  const domSelfNames = () => {
    try {
      const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
      const isHandle = (t) => /^[a-z0-9_.]{2,32}$/i.test(t);
      const gear = document.querySelector('[aria-label*="User Settings" i]');
      let panel = null;
      if (gear) { let n = gear; for (let i = 0; i < 8 && n; i++) { n = n.parentElement; if (n && n.querySelector('img[src*="/avatars/"]')) { panel = n; break; } } }
      if (!panel) return null;
      const img = panel.querySelector('img[src*="/avatars/"]');
      const idm = img && (img.getAttribute("src") || "").match(/\/avatars\/(\d{15,21})\//);
      const titleEl = panel.querySelector('[class*="panelTitle"]');
      const display = titleEl ? clean(titleEl.textContent) : null;
      let username = null;     // accept only a clean handle, never status/activity text
      for (const e of panel.querySelectorAll('[class*="hovered"]')) {
        const t = clean(e.textContent);
        if (t && isHandle(t) && (!display || t.toLowerCase() !== display.toLowerCase())) { username = t; break; }
      }
      return { selfId: idm ? idm[1] : null, globalName: display || null, username: username || null };
    } catch (e) { return null; }
  };

  // ======== public API: PRIMARY (webpack) first, FALLBACK (DOM) second ========
  return {
    speaking: () => {
      const w = wpSpeaking();
      if (w !== null) return w;
      const r = roster(); const me = domSelf().selfId;
      return Object.keys(r).filter((uid) => r[uid].speaking && uid !== me);
    },
    voiceStates: () => {
      const w = wpVoiceStates();
      if (w !== null) return w;
      const r = roster(); const uids = Object.keys(r);
      if (!uids.length) return null;
      const o = {};
      for (const uid of uids) {
        const e = r[uid];
        o[uid] = { selfMute: !!e.selfMute, selfDeaf: !!e.selfDeaf, mute: !!e.mute, deaf: !!e.deaf,
                   video: !!e.video, stream: !!e.stream, suppress: !!e.suppress };
      }
      return o;
    },
    self: () => {
      let base = wpSelf();
      if (!base) base = domSelf();
      else if (!base.speakingReliable) {
        const d = domSelf();
        if (d && d.speakingReliable) { base.speaking = d.speaking; base.speakingReliable = true; }
      }
      if (!base) return null;
      // Backfill identity names from the DOM when the webpack stores didn't supply them (kept as the
      // backup). globalName + username are enough for keyword suggestions; server nick stays best-effort.
      if (!base.globalName || !base.username) {
        const dn = domSelfNames();
        if (dn) {
          if (!base.globalName && dn.globalName) base.globalName = dn.globalName;
          if (!base.username && dn.username) base.username = dn.username;
          if (!base.selfId && dn.selfId) base.selfId = dn.selfId;
        }
      }
      return base;
    },
    ssrcMap: () => wpSsrcMap(),
    user: (uid) => {
      const w = wpUser(uid);
      if (w) return w;
      const r = roster();
      if (r[uid] && r[uid].name) return { userId: uid, name: r[uid].name, avatar: r[uid].avatar };
      return avatarScrape(uid);
    },
  };
})();
"""

def speaking_users(cdp):
    return cdp.evaluate(_BOOT + "window.__vtr.speaking()") or []

def user_info(cdp, uid):
    return cdp.evaluate(_BOOT + "window.__vtr.user(" + json.dumps(uid) + ")")

def self_state(cdp):
    return cdp.evaluate(_BOOT + "window.__vtr.self()")

def voice_states(cdp):
    return cdp.evaluate(_BOOT + "window.__vtr.voiceStates()")

def ssrc_map(cdp):
    """{'map': {ssrc(str): {userId, kind}}, 'audio': [ssrc,...]} or None.
    `kind` is 'voice' | 'video' | 'stream'. `audio` lists audio ssrcs for the
    native offset auto-locator. Keys come back stringified from JSON."""
    return cdp.evaluate(_BOOT + "window.__vtr.ssrcMap()")


OVERLAY_CLEANUP_JS = r"""
(() => {
  let removed = 0;
  try {
    if (window.__vtOverlay && window.__vtOverlay.destroy) window.__vtOverlay.destroy();
  } catch (e) {}
  try {
    document.querySelectorAll('.vt-container,.vtlog,.vt-status,#vt-style').forEach((el) => {
      removed++;
      el.remove();
    });
  } catch (e) {}
  try {
    delete window.__vtOverlay;
    delete window.__VT_CONFIG;
  } catch (e) {}
  return removed;
})()
"""


def cleanup_overlay(cdp):
    return cdp.evaluate(OVERLAY_CLEANUP_JS)
