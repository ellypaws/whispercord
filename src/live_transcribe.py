"""
Live per-user Discord transcription (BetterDiscord-free core).
Pipeline:  Frida hook on discord_voice.node (ChannelReceive::GetAudioFrameWithInfo)
        -> per-user 16 kHz mono PCM
        -> faster-whisper on GPU (per-user utterances, split on silence)
        -> WebSocket relay on 127.0.0.1:8765  ({type:'transcript', userId, name, text, isFinal})
The overlay (BD plugin or the standalone window) connects to the relay and renders.

Env:
  WHISPER_MODEL   faster-whisper model (default 'small'; use 'large-v3' for best quality on a 3090)
  RELAY_PORT      websocket port (default 8765)
"""
import os, sys, re, glob, time, json, threading, collections, asyncio

# Windows consoles default to cp1252; transcripts contain emoji/CJK. Never crash on print.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- make pip CUDA DLLs (cuBLAS/cuDNN) discoverable on Windows (packaging-friendly) ---
def add_cuda_dlls():
    dirs = glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin"))
    base = getattr(sys, "_MEIPASS", None)          # frozen: DLLs bundled under the temp dir
    if base:
        dirs += [base]
        dirs += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    try:
        import cuda_setup                          # first-run-downloaded CUDA DLLs
        dirs.append(cuda_setup.cuda_dir())
    except Exception:
        pass
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
add_cuda_dlls()

import numpy as np
import frida
import websockets
from faster_whisper import WhisperModel

import paths
from config import load as _load_config
from locate import locate_rva       # runtime RVA auto-locator (survives Discord updates)
CFG = _load_config()
MODEL = CFG["whisper_model"]
LANGUAGE = (CFG.get("language") or "").strip() or None
BEAM = int(CFG.get("beam_size", 1))
DEVICE = CFG.get("device", "cuda")
COMPUTE = CFG.get("compute_type", "float16")
RELAY_PORT = CFG["relay_port"]
SILENCE_S = CFG["silence_s"]        # gap that ends an utterance
MIN_UTT_S = CFG["min_utt_s"]
MAX_UTT_S = CFG["max_utt_s"]
INTERIM_EVERY = CFG["interim_every_s"]  # re-transcribe a growing utterance this often (live partials)

# --- silence/hallucination gating (kills "Thank you." on quiet audio) ---
GATE = CFG["gating"]
GATE_DBFS = GATE["min_rms_dbfs"]
USE_VAD = GATE["vad"]
NO_SPEECH = GATE["no_speech_threshold"]
MIN_LOGPROB = GATE["min_avg_logprob"]
def _norm(s):
    return s.lower().strip().strip(".!?,…\"' ").strip()
DROP = set(_norm(p) for p in GATE["drop_phrases"])

# ---------------- audio capture (Frida) ----------------
buffers = collections.defaultdict(bytearray)   # src -> int16 mono 16k bytes
last_frame = collections.defaultdict(float)
announced = {}                                 # src -> bool (placeholder shown this utterance)
interim_at = collections.defaultdict(float)    # src -> last interim transcribe time
src2user = {}                                  # src -> {userId, name, avatar}
src_client = {}                                # src -> client exe name (e.g. 'discordptb.exe')
hooked_clients = set()                         # client exe names the Frida hook attached to
cdp_clients = set()                            # client exe names with a live CDP connection
self_gate = {}                                 # client -> bool: capture my mic for this client right now
corr = collections.defaultdict(dict)           # src -> {uid: co-occurrence score} for speaker binding
lock = threading.Lock()

SELF = CFG.get("self_transcribe", {})          # own-voice transcription options
DEBUG_BIND = os.environ.get("VT_DEBUG_BIND") == "1"   # verbose speaker-binding correlation log

# persistent cache of resolved users (userId -> {userId,name,avatar}) so names survive restarts
user_cache = {}
CACHE_PATH = paths.data("cache", "users.json")
def load_user_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            user_cache.update(json.load(f))
        print("[cache] %d users loaded" % len(user_cache))
    except Exception:
        pass
def save_user_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(user_cache, f)
    except Exception:
        pass

JS = r"""
let MOD = null;
function modpath() {
  MOD = Process.enumerateModules().find(m => /discord_voice/i.test(m.name)) || null;
  return MOD ? MOD.path : null;
}
function install(rva) {
  if (!MOD) modpath();
  if (!MOD) return false;
  const PID = Process.id;
  const CLIENT = ((Process.enumerateModules()[0] || {}).name || "").toLowerCase();  // e.g. discordptb.exe
  Interceptor.attach(MOD.base.add(rva), {
    onEnter(a) { this.src = a[0]; this.frame = a[2]; },
    onLeave(r) {
      if (r.toInt32() !== 0) return;                      // kNormal only
      const f = this.frame;
      const spc = f.add(0x18).readU32(), ch = f.add(0x28).readU32();
      const base = f.add(0x50);                            // data_
      const outN = Math.floor(spc / 3);                   // 48k -> 16k
      const buf = new ArrayBuffer(outN * 2);
      const view = new Int16Array(buf);
      for (let i = 0; i < outN; i++) view[i] = base.add(i * 3 * ch * 2).readS16();  // L channel
      send({ src: PID + ':' + this.src.toString(), client: CLIENT }, buf);  // tag by client (multi-client safe)
    }
  });
  send({ ready: true, pid: PID, client: CLIENT });
  return true;
}
rpc.exports = { modpath: modpath, install: install };
"""

def on_message(msg, data):
    if msg.get("type") != "send":
        print("[frida]", msg.get("description") or msg); return
    p = msg["payload"]
    if p.get("ready"):
        print("[capture] hook installed (pid %s)" % p.get("pid")); return
    src = p.get("src")
    if src and data:
        with lock:
            buffers[src].extend(data)
            last_frame[src] = time.time()
            if p.get("client"):
                src_client[src] = p["client"]

def attach_hook():
    """Attach to EVERY Discord-family process (Discord/PTB/Canary/Dev) that has
    discord_voice loaded, locating the RVA per-module. Returns kept-alive sessions."""
    dev = frida.get_local_device()
    sessions = []
    seen = set()
    for pr in dev.enumerate_processes():
        if not re.search(r"discord", pr.name, re.I):
            continue
        try:
            s = frida.attach(pr.pid)
            sc = s.create_script(JS)
            sc.on("message", on_message)
            sc.load()
            path = sc.exports_sync.modpath()
            if not path:
                s.detach(); continue
            rva, _ = locate_rva(path)
            if sc.exports_sync.install(rva):
                print("[capture] %s PID %d  RVA 0x%x" % (pr.name, pr.pid, rva))
                sessions.append((s, sc)); seen.add(pr.name); hooked_clients.add(pr.name.lower())
            else:
                s.detach()
        except Exception:
            continue
    if not sessions:
        raise RuntimeError("no discord_voice process found "
                           "(is a Discord client running and in a voice call?)")
    print("[capture] hooked %d process(es): %s" % (len(sessions), ", ".join(sorted(seen))))
    return sessions

# ---------------- own-voice capture (microphone, gated by Discord state) ----------------
def self_capture_thread():
    """Capture the local mic and route it to each client whose self-gate is open
    (set by mapping_thread from Discord's speaking/mute state). Keyed 'self:<client>'."""
    if not SELF.get("enabled"):
        return
    try:
        import sounddevice as sd
    except Exception as e:
        print("[self] sounddevice unavailable (%s); own-voice disabled" % e)
        return
    dev = SELF.get("device")

    def push(b16):
        now = time.time()
        with lock:
            for client, open_ in self_gate.items():
                if open_:
                    key = "self:" + client
                    buffers[key].extend(b16)
                    last_frame[key] = now
                    src_client[key] = client

    def cb16(indata, frames, tinfo, status):
        push(indata.tobytes())

    def cb48(indata, frames, tinfo, status):
        push(np.ascontiguousarray(indata[::3, 0]).tobytes())   # 48k -> 16k (L)

    for sr, ch, cb in ((16000, 1, cb16), (48000, 1, cb48)):
        try:
            stream = sd.InputStream(samplerate=sr, channels=ch, dtype="int16",
                                    blocksize=int(sr * 0.1), device=dev, callback=cb)
            stream.start()
            print("[self] mic capture @%dHz (device=%s)" % (sr, dev if dev is not None else "default"))
            while True:
                time.sleep(1)
        except Exception as e:
            print("[self] mic open @%dHz failed: %s" % (sr, e))
    print("[self] could not open the microphone; own-voice disabled")


# ---------------- relay (WebSocket) ----------------
clients = set()
relay_loop = None

def start_relay():
    global relay_loop
    async def handler(ws):
        clients.add(ws)
        print("[relay] overlay connected")
        try:
            await ws.wait_closed()
        finally:
            clients.discard(ws)
    async def main():
        global relay_loop
        relay_loop = asyncio.get_event_loop()
        async with websockets.serve(handler, "127.0.0.1", RELAY_PORT):
            print("[relay] ws://127.0.0.1:%d" % RELAY_PORT)
            await asyncio.Future()
    asyncio.run(main())

def broadcast(obj):
    if relay_loop is None:
        return
    data = json.dumps(obj)
    for ws in list(clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send(data), relay_loop)
        except Exception:
            pass

# ---------------- src -> real user mapping (via CDP speaking correlation) ----------------
def inject_overlay(c, client=None):
    """Inject the BetterDiscord-free overlay into one client's renderer.
    `client` scopes the overlay so it only shows that client's own call."""
    with open(paths.resource("overlay.js"), encoding="utf-8") as f:
        ov = f.read()
    cfg_js = "window.__VT_CONFIG=" + json.dumps(
        {"overlay": CFG["overlay"], "alerts": CFG["alerts"],
         "relay_port": RELAY_PORT, "client": client}) + ";"
    c.evaluate("(window.BdApi&&BdApi.Plugins&&BdApi.Plugins.isEnabled&&BdApi.Plugins.isEnabled('VoiceTranscriber'))?BdApi.Plugins.disable('VoiceTranscriber'):0")
    c.evaluate("try{window.__vtOverlay&&window.__vtOverlay.destroy&&window.__vtOverlay.destroy()}catch(e){};document.querySelectorAll('.vt-container,.vtlog,.vt-status,#vt-style').forEach(e=>e.remove());")
    c.evaluate(cfg_js)
    c._cmd("Page.enable", {}, c.session)
    c._cmd("Page.addScriptToEvaluateOnNewDocument", {"source": cfg_js + ov}, c.session)
    return c.evaluate(ov)


def inject_for(client):
    v = CFG.get("inject_overlay", True)
    if isinstance(v, dict):
        return v.get(client, True)
    return bool(v)


def self_enabled_for(client):
    if not SELF.get("enabled"):
        return False
    return (SELF.get("clients") or {}).get(client, True)


def mapping_thread():
    from cdp import CDP, speaking_users, user_info, self_state, voice_states
    from launch import CLIENTS
    port2client = {port: exe.lower() for _, (exe, port) in CLIENTS.items()}  # 9223 -> 'discordptb.exe'

    ports = CFG.get("cdp_ports") or [CFG["cdp_port"]]
    master_inject = os.environ.get("VT_INJECT_OVERLAY", "1") == "1"
    conns = {}                                  # port -> {client, cdp, fails}
    injected = set()                            # clients already injected (avoid re-inject spam)

    def try_connect():
        for port in ports:
            if port in conns:
                continue
            try:
                c = CDP(port)
            except Exception:
                continue
            client = port2client.get(port)
            conns[port] = {"client": client, "cdp": c, "fails": 0}
            if client:
                cdp_clients.add(client)
            print("[map] CDP connected on port %d (%s)" % (port, client or "unknown"))
            if master_inject and inject_for(client) and client not in injected:
                try:
                    inject_overlay(c, client); injected.add(client)
                    print("[overlay] injected (%s)" % (client or "all"))
                except Exception as e:
                    print("[overlay] inject failed (%s): %s" % (client, e))

    def resolve_user(c, uid):
        info = user_cache.get(uid)
        if not info:
            try:
                info = user_info(c, uid)            # resolve via the SAME client
            except Exception:
                info = None
            if info:
                user_cache[uid] = info; save_user_cache()
        return info

    def bind(s, uid, client, c):
        if src2user.get(s, {}).get("userId") == uid:
            return
        info = resolve_user(c, uid)
        if info:
            src2user[s] = info
            print("[map] %s [%s] -> %s" % (s[-6:], client, info["name"]))
            broadcast({"type": "rename", "userId": s, "name": info["name"],
                       "avatar": info["avatar"], "client": client})

    def emit_event(kind, uid, client, c):
        info = resolve_user(c, uid) or {"name": "user " + uid[-4:], "avatar": None}
        broadcast({"type": "event", "event": kind, "client": client, "userId": uid,
                   "name": info.get("name"), "avatar": info.get("avatar"),
                   "ts": int(time.time() * 1000)})

    def diff_voice(client, c, prev, cur):
        for uid in cur:
            if uid not in prev:
                emit_event("joined", uid, client, c)
        for uid in prev:
            if uid not in cur:
                emit_event("left", uid, client, c)
        for uid in cur:
            a = prev.get(uid)
            if not a:
                continue
            b = cur[uid]
            if a.get("selfMute") != b.get("selfMute"):
                emit_event("muted" if b.get("selfMute") else "unmuted", uid, client, c)
            if a.get("selfDeaf") != b.get("selfDeaf"):
                emit_event("deafened" if b.get("selfDeaf") else "undeafened", uid, client, c)
            if a.get("video") != b.get("video"):
                emit_event("video_on" if b.get("video") else "video_off", uid, client, c)
            if a.get("stream") != b.get("stream"):
                emit_event("stream_on" if b.get("stream") else "stream_off", uid, client, c)

    try_connect()
    if not conns:
        print("[map] no CDP yet - retrying every 3s; placeholder names until a client opens its debug port")

    last_probe = time.time()
    while True:
        time.sleep(0.25)
        now = time.time()
        if now - last_probe > 3:                # re-probe for newly-opened ports (e.g. you restarted Canary)
            last_probe = now
            try_connect()
        # Correlate INDEPENDENTLY per client: a stream from client X can only ever bind
        # to a speaker reported by client X's own CDP. No cross-client leakage.
        for port, st0 in list(conns.items()):
            client, c = st0["client"], st0["cdp"]
            try:
                speaking = set(speaking_users(c))
                st0["fails"] = 0
            except Exception:
                st0["fails"] += 1
                if st0["fails"] >= 5:           # tolerate transient errors; drop only after sustained failure
                    conns.pop(port, None)
                    if client:
                        cdp_clients.discard(client)
                    injected.discard(client)
                    print("[map] CDP on port %d dropped; will retry" % port)
                continue

            # own-voice gate (per client): only capture my mic when Discord agrees I'm speaking/unmuted
            if client and self_enabled_for(client):
                try:
                    sst = self_state(c)
                except Exception:
                    sst = None
                open_ = False
                if sst and sst.get("inCall"):
                    ok_mute = (not SELF.get("only_when_unmuted", True)) or (not sst.get("muted"))
                    ok_speak = (not SELF.get("require_discord_speaking", True)) or sst.get("speaking")
                    open_ = bool(ok_mute and ok_speak)
                self_gate[client] = open_
                key = "self:" + client
                if open_ and key not in src2user and sst:
                    info = user_cache.get(sst["selfId"])
                    if not info:
                        try:
                            info = user_info(c, sst["selfId"])
                        except Exception:
                            info = None
                    if info:
                        src2user[key] = info
            elif client:
                self_gate[client] = False

            # voice events: diff this client's channel voice-states ~1x/sec
            if CFG.get("voice_events", True) and client and now - st0.get("vs_t", 0) > 1.0:
                st0["vs_t"] = now
                try:
                    cur_vs = voice_states(c)
                except Exception:
                    cur_vs = None
                if cur_vs is None:
                    st0["vs_prev"] = None            # not in a call; reset so re-join re-seeds
                elif st0.get("vs_prev") is None:
                    st0["vs_prev"] = cur_vs           # seed silently (no events on first snapshot)
                else:
                    diff_voice(client, c, st0["vs_prev"], cur_vs)
                    st0["vs_prev"] = cur_vs

            with lock:
                active = [s for s, t in last_frame.items()
                          if now - t < 0.4 and (client is None or src_client.get(s) == client)]
            if not speaking or not active:
                continue
            # Correlate (active stream) with (speaking user) using positive AND negative evidence:
            #  + a stream active while a user speaks is evidence they own it (extra when it's a
            #    clean 1-speaker/1-stream moment),
            #  - a stream active while a candidate is SILENT is evidence against them.
            # The true owner speaks whenever their stream is active, so they pull clear even when
            # several people talk over each other continuously (where plain co-occurrence ties).
            spk = set(speaking)
            solo = (len(spk) == 1 and len(active) == 1)
            for s in active:
                sc = corr[s]
                for uid in spk:
                    sc[uid] = min(30.0, sc.get(uid, 0.0) + (2.0 if solo else 1.0))
                for uid in list(sc.keys()):
                    if uid not in spk and sc[uid] > 0:
                        sc[uid] = max(0.0, sc[uid] - 0.5)
            # Bind with mutual exclusion: one user owns at most one active stream. A continuous
            # talker (soundboard/music) would otherwise score high on every stream; their OWN
            # stream accumulates fastest (active on every tick they talk) so it binds first, then
            # they're off the table and the remaining streams fall to their real owners.
            active_set = set(active)
            taken = {}
            for s2, info in list(src2user.items()):
                u2 = info.get("userId") if isinstance(info, dict) else None
                if u2 and s2 in active_set:
                    taken[u2] = s2
            for s in active:
                sc = corr.get(s)
                if not sc:
                    continue
                cands = {u: v for u, v in sc.items() if taken.get(u, s) == s}
                if not cands:
                    continue
                best = max(cands, key=cands.get); bestv = cands[best]
                second = max((v for u, v in cands.items() if u != best), default=0.0)
                cur = (src2user.get(s) or {}).get("userId")
                # hysteresis: keep an existing binding unless a challenger clearly beats it,
                # so a transient blip doesn't make a name flap between users
                if cur is not None and best != cur and bestv < cands.get(cur, 0.0) * 1.3 + 2:
                    continue
                if bestv >= second * 1.5 + 1 and (bestv >= 2 if solo else bestv >= 4):
                    bind(s, best, client, c); taken[best] = s
            if DEBUG_BIND and now - st0.get("dbg_t", 0) > 2.0:
                st0["dbg_t"] = now
                summ = []
                for s in active:
                    sc = corr.get(s) or {}
                    top = sorted(sc.items(), key=lambda kv: -kv[1])[:3]
                    summ.append("%s:{%s}" % (s[-5:], ",".join("%s=%.1f" % (u[-4:], v) for u, v in top)))
                print("[dbg %s] spk=%d(%s) active=%d solo=%s | %s" % (
                    client, len(spk), ",".join(u[-4:] for u in list(spk)[:4]),
                    len(active), solo, " ".join(summ)))

# ---------------- transcription ----------------
def main():
    load_user_cache()
    if DEVICE == "cuda":
        try:
            from cuda_setup import ensure_cuda, cuda_present
            if not cuda_present():
                print("[cuda] GPU runtime not found - downloading (~1 GB, first run only)...")
                ensure_cuda(print)
            add_cuda_dlls()
        except Exception as e:
            print("[cuda] setup failed (%s) - GPU may not load" % e)
    print("[whisper] loading '%s' on %s (%s)..." % (MODEL, DEVICE, COMPUTE))
    model = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE)
    print("[whisper] ready (lang=%s, beam=%d)" % (LANGUAGE or "auto", BEAM))

    threading.Thread(target=start_relay, daemon=True).start()
    threading.Thread(target=mapping_thread, daemon=True).start()
    threading.Thread(target=self_capture_thread, daemon=True).start()
    sess = attach_hook()  # keep ref alive

    def rms_dbfs(audio):
        if audio.size == 0:
            return -120.0
        r = float(np.sqrt(np.mean(audio * audio)))
        return 20.0 * np.log10(max(r, 1e-7))

    def transcribe(b):
        audio = np.frombuffer(bytes(b), dtype=np.int16).astype(np.float32) / 32768.0
        level = rms_dbfs(audio)
        if level < GATE_DBFS:                          # too quiet to be speech -> skip the model
            return ""
        segs, _ = model.transcribe(
            audio, beam_size=BEAM, language=LANGUAGE,
            vad_filter=USE_VAD,
            vad_parameters={"min_silence_duration_ms": 300} if USE_VAD else None,
            no_speech_threshold=NO_SPEECH,
            condition_on_previous_text=False,          # avoid repeat/hallucination loops
        )
        out = []
        for s in segs:
            txt = s.text.strip()
            if not txt:
                continue
            low_conf = (s.no_speech_prob > NO_SPEECH) or (s.avg_logprob < MIN_LOGPROB)
            # known silence-hallucination phrase + (low confidence or near-quiet) -> drop
            if _norm(txt) in DROP and (low_conf or level < GATE_DBFS + 8):
                continue
            out.append(txt)
        return " ".join(out).strip()

    def emit(src, text, final):
        u = src2user.get(src)
        name = u["name"] if u else ("user " + src[-5:])
        avatar = u["avatar"] if u else None
        broadcast({"type": "transcript", "userId": src, "name": name, "avatar": avatar,
                   "text": text, "isFinal": final, "client": src_client.get(src),
                   "ts": int(time.time() * 1000)})

    def heartbeat():
        while True:
            time.sleep(1.5)
            now = time.time()
            with lock:
                per = {}
                for s, t in last_frame.items():
                    cl = src_client.get(s, "?")
                    d = per.setdefault(cl, {"streams": 0, "active": 0, "mapped": 0})
                    d["streams"] += 1
                    if now - t < 0.6:
                        d["active"] += 1
                    if s in src2user:
                        d["mapped"] += 1
                total_active = sum(d["active"] for d in per.values())
            clients = {}
            for cl in (set(hooked_clients) | set(cdp_clients) | set(per.keys())) - {"?"}:
                d = per.get(cl, {"streams": 0, "active": 0, "mapped": 0})
                clients[cl] = {"hooked": cl in hooked_clients, "cdp": cl in cdp_clients,
                               "streams": d["streams"], "active": d["active"], "mapped": d["mapped"]}
            broadcast({"type": "status", "state": "listening", "active": total_active,
                       "mapped": len(src2user), "clients": clients})
    threading.Thread(target=heartbeat, daemon=True).start()

    print("[run] live transcription running (Ctrl+C to stop)")
    while True:
        time.sleep(0.2)
        now = time.time()
        jobs = []  # (src, bytes|None, kind)
        with lock:
            for src, b in list(buffers.items()):
                dur = len(b) / 2 / 16000.0
                silent = now - last_frame[src] >= SILENCE_S
                if silent:
                    if dur >= MIN_UTT_S:
                        jobs.append((src, bytes(b), "final"))
                    buffers[src] = bytearray(); announced.pop(src, None); interim_at.pop(src, None)
                else:
                    if not announced.get(src):
                        announced[src] = True
                        jobs.append((src, None, "start"))        # instant "speaking…" feedback
                    if dur >= MAX_UTT_S:
                        jobs.append((src, bytes(b), "final"))     # cap runaway utterances
                        buffers[src] = bytearray(); announced.pop(src, None); interim_at.pop(src, None)
                    elif dur >= MIN_UTT_S and now - interim_at[src] >= INTERIM_EVERY:
                        interim_at[src] = now
                        jobs.append((src, bytes(b), "interim"))   # live partial
        for src, b, kind in jobs:
            if kind == "start":
                emit(src, "", False)
            elif kind == "interim":
                t = transcribe(b)
                if t:
                    emit(src, t, False)
            elif kind == "final":
                t = transcribe(b)
                if t:
                    name = (src2user.get(src) or {}).get("name", "user " + src[-5:])
                    print("  [%s] %s" % (name, t)); emit(src, t, True)

if __name__ == "__main__":
    main()
