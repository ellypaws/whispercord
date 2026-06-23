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
    // Resolved stores never move for the life of the renderer, so cache them. The fallback path
    // below scans the ENTIRE webpack module map per lookup (7 stores x every poll); memoizing the
    // hits collapses that to a one-time cost. Cold clients (no webpack) early-return null cheaply.
    const sc = (window.__vtSC = window.__vtSC || {});
    if (sc[name]) return sc[name];
    let found = null;
    if (window.BdApi && BdApi.Webpack && BdApi.Webpack.getStore) {
      try { const s = BdApi.Webpack.getStore(name); if (s) found = s; } catch (e) {}
    }
    if (!found) {
      const req = getReq(); const c = req && req.c;
      if (c) for (const k in c) {
        let e; try { e = c[k] && c[k].exports; } catch (er) { continue; }
        if (!e) continue;
        try { if (e.getName && e.getName() === name) { found = e; break; } } catch (er) {}
        try { if (e.default && e.default.getName && e.default.getName() === name) { found = e.default; break; } } catch (er) {}
      }
    }
    if (found) sc[name] = found;
    return found;
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
  // Resolve the server nickname for `uid` from the guild of the channel THIS client is connected to.
  // Returns { nick, gAvatar, guildId }; nick is null when the member record isn't loaded yet.
  const wpNick = (s, uid) => {
    let guildId = null, nick = null, gAvatar = null;
    try {
      const chId = s.RCS && s.RCS.getChannelId && s.RCS.getChannelId();
      const ch = chId && s.CS && s.CS.getChannel && s.CS.getChannel(chId);
      guildId = ch && ch.guild_id;
      if (guildId && s.GMS) {
        if (s.GMS.getMember) { const m = s.GMS.getMember(guildId, uid); if (m) { nick = m.nick || null; gAvatar = m.avatar || null; } }
        if (!nick && s.GMS.getNick) { try { nick = s.GMS.getNick(guildId, uid) || null; } catch (e) {} }
      }
    } catch (e) {}
    return { nick: nick, gAvatar: gAvatar, guildId: guildId };
  };
  // Return the name COMPONENTS (nick / globalName / username) separately so the public resolver can
  // apply the preference order across every source, rather than collapsing to one string too early
  // (a partial UserStore record commonly has username but no globalName/nick until the profile loads).
  const wpUser = (uid) => {
    const s = stores(); if (!s.US) return null;
    try {
      const u = s.US.getUser && s.US.getUser(uid); if (!u) return null;
      const n = wpNick(s, uid);
      const globalName = u.globalName || u.global_name || null;
      const username = u.username || null;
      return { userId: u.id, nick: n.nick, globalName: globalName, username: username,
               name: n.nick || globalName || username, avatar: avatarUrl(u, { avatar: n.gAvatar }, n.guildId) };
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
  const isChId = (v) => typeof v === "string" ? /^\d{15,21}$/.test(v) : (typeof v === "number" && v > 0);
  // our own user id from the bottom-left account avatar (DOM-only, no webpack needed)
  const getSelfId = () => {
    try {
      const re = /\/avatars\/(\d{15,21})\//;
      const accImg = document.querySelector(
        '[class*="accountProfile"] img[src*="/avatars/"], [class*="avatarWrapper"] img[src*="/avatars/"], section[class*="panel"] img[src*="/avatars/"]');
      if (accImg) { const m = (accImg.src || "").match(re); if (m) return m[1]; }
    } catch (e) {}
    return null;
  };
  // The voice channel THIS client is actually CONNECTED to (not whatever channel is open on
  // screen). Anchored off the visible Disconnect button in the connected-voice panel: walk its
  // fibers for a channelId / channel object, then fall back to a /channels/<guild>/<id> link.
  // Returns a string id, or null when not connected / not derivable.
  const connChannelId = () => {
    try {
      const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const disc = Array.from(document.querySelectorAll('button[aria-label],[role="button"][aria-label]'))
        .find((b) => vis(b) && /^disconnect$/i.test(b.getAttribute("aria-label") || ""));
      if (!disc) return null;
      let f = fiberOf(disc), hops = 0;
      while (f && hops < 40) {
        const p = f.memoizedProps;
        if (p) {
          if (isChId(p.channelId)) return String(p.channelId);
          if (p.channel && isChId(p.channel.id)) return String(p.channel.id);
        }
        f = f.return; hops++;
      }
      let n = disc;
      for (let i = 0; i < 10 && n; i++) {
        n = n.parentElement; if (!n) break;
        const a = n.querySelector('a[href*="/channels/"]');
        if (a) { const m = (a.getAttribute("href") || "").match(/\/channels\/[^/]+\/(\d{15,21})/); if (m) return m[1]; }
      }
    } catch (e) {}
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
          const cid = (p.channel && p.channel.id != null) ? String(p.channel.id) : null;
          for (const uid in p.voiceStates) {
            const e = p.voiceStates[uid]; if (!e) continue;
            const u = e.user; if (!u || !u.id) continue;
            const vs = e.voiceState || {};
            const nick = e.nick || (e.member && e.member.nick) || null;
            const globalName = u.globalName || u.global_name || null;
            const nm = nick || globalName || u.username;
            const o = out[u.id] || (out[u.id] = { uid: u.id });
            o.name = nm; o.username = u.username; o.nick = nick; o.globalName = globalName;
            o.channelId = cid;                 // which voice channel this person is in
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
      let f = fiberOf(t), hops = 0, u = null, nick = null, fsp = false, chId = null;
      // keep walking up past the user fiber so we can also read the enclosing channel id
      while (f && hops < 20) {
        const p = f.memoizedProps;
        if (p) {
          if (!u && p.user && p.user.id) { u = p.user; nick = p.nick; fsp = p.speaking === true; }
          if (chId === null && p.channel && p.channel.id != null) chId = String(p.channel.id);
        }
        if (u && chId !== null) break;
        f = f.return; hops++;
      }
      if (!u) return;
      const o = out[u.id] || (out[u.id] = { uid: u.id });
      const tnick = nick || u.nick || null;
      const tglobal = u.globalName || u.global_name || null;
      if (o.nick == null) o.nick = tnick;
      if (o.globalName == null) o.globalName = tglobal;
      if (o.name === undefined) o.name = tnick || tglobal || u.username;
      if (o.username === undefined) o.username = u.username;
      if (o.channelId == null && chId !== null) o.channelId = chId;
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
  // A predicate selecting only the people in MY voice channel, or null when I'm not in a channel
  // that is on screen. You can only be in one voice channel, and your own id always appears in its
  // roster, so the channel containing selfId IS my channel. Anything else (a channel I'm only
  // previewing, or none) yields null -> callers report nobody, never the wrong channel.
  const myChannelFilter = (r, selfId) => {
    if (!selfId || !r[selfId]) return null;
    const mine = r[selfId].channelId != null ? String(r[selfId].channelId) : "_";
    return (uid) => {
      const c = (r[uid] && r[uid].channelId != null) ? String(r[uid].channelId) : "_";
      return c === mine;
    };
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
    const selfId = getSelfId();
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
    let channelId = null;
    if (selfId) {
      const r = roster();
      const me = r[selfId];
      if (me) {
        speaking = !!(me.domSpeaking || me.fiberSpeaking);
        speakingReliable = true;
        if (me.channelId != null) channelId = String(me.channelId);
      }
    }
    // Prefer the connected-voice panel's channel id; it is right even when the call is off screen.
    if (!channelId) { const cc = connChannelId(); if (cc) channelId = cc; }
    // inCall is authoritative (the visible Disconnect button). channelId is the real connected
    // channel when we could read it, else 1 as a "in a call, id unknown" sentinel.
    // reliable:false means values were scraped from the DOM; the own-voice gate fails closed on an
    // unreliable read so a muted/background client won't leak.
    return { selfId: selfId, channelId: inCall ? (channelId || 1) : (channelId || null),
             inCall: inCall, muted: muted, deaf: deaf, speaking: speaking,
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
      const r = roster(); const me = getSelfId();
      const inMine = myChannelFilter(r, me);
      if (!inMine) return [];                       // not in any on-screen channel -> nobody
      return Object.keys(r).filter((uid) => uid !== me && r[uid].speaking && inMine(uid));
    },
    voiceStates: () => {
      const w = wpVoiceStates();
      if (w !== null) return w;
      const r = roster(); const me = getSelfId();
      const inMine = myChannelFilter(r, me);
      // null (not in a visible channel) is treated by the engine as a transient off-screen read,
      // so it won't wipe the roster baseline or fire bogus leave events.
      if (!inMine) return null;
      const uids = Object.keys(r).filter(inMine);
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
      // Resolve from EVERY source, then apply the preference (server nick -> global display name ->
      // username) across all of them. wpUser alone is not enough: a partial UserStore record often
      // carries only the username until that user's profile/member loads (i.e. their tile renders),
      // which is why the name used to flip to the bare username "depending on where the user is".
      const w = wpUser(uid);
      const r = roster()[uid];
      const nick = (w && w.nick) || (r && r.nick) || null;
      const globalName = (w && w.globalName) || (r && r.globalName) || null;
      const username = (w && w.username) || (r && r.username) || null;
      const avatar = (w && w.avatar) || (r && r.avatar) || null;
      const name = nick || globalName || username;
      if (name) return { userId: uid, name: name, nick: nick, globalName: globalName,
                         username: username, avatar: avatar };
      return avatarScrape(uid);   // last resort: avatar alt text (usually the username)
    },
  };
})();
"""

# Inject the __vtr bootstrap only when it's missing (fresh renderer or after a reload). Re-running
# the whole IIFE on every poll - rebuilding all closures and re-pushing the webpack chunk - was the
# bulk of the per-tick renderer cost; the guard ships the text but the engine skips it once __vtr
# exists, so each call is self-healing without paying for re-execution.
_ENSURE = "if(!window.__vtr){\n" + _BOOT + "\n}\n"

def poll_state(cdp, vs=False, ssrc=False):
    """Per-tick hot path in ONE round-trip: speaking + self, plus optional voiceStates/ssrcMap.
    Entering the renderer once instead of 2-4 times per tick cuts blocking and main-thread tasks.
    Returns {'s': [...], 'm': {...}, 'v': {...}?, 'r': {...}?} or None on evaluate failure."""
    parts = ["s:window.__vtr.speaking()", "m:window.__vtr.self()"]
    if vs:   parts.append("v:window.__vtr.voiceStates()")
    if ssrc: parts.append("r:window.__vtr.ssrcMap()")
    return cdp.evaluate(_ENSURE + "({" + ",".join(parts) + "})")

def speaking_users(cdp):
    return cdp.evaluate(_ENSURE + "window.__vtr.speaking()") or []

def user_info(cdp, uid):
    return cdp.evaluate(_ENSURE + "window.__vtr.user(" + json.dumps(uid) + ")")

def self_state(cdp):
    return cdp.evaluate(_ENSURE + "window.__vtr.self()")

def voice_states(cdp):
    return cdp.evaluate(_ENSURE + "window.__vtr.voiceStates()")

def ssrc_map(cdp):
    """{'map': {ssrc(str): {userId, kind}}, 'audio': [ssrc,...]} or None.
    `kind` is 'voice' | 'video' | 'stream'. `audio` lists audio ssrcs for the
    native offset auto-locator. Keys come back stringified from JSON."""
    return cdp.evaluate(_ENSURE + "window.__vtr.ssrcMap()")


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
