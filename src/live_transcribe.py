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

import pkg_setup
pkg_setup.prepare()          # put downloaded ctranslate2/onnxruntime (+ av stub) on sys.path

import numpy as np
import frida
import websockets
# faster_whisper (+ its unbundled ctranslate2/onnxruntime/av deps) is imported lazily in main()'s
# CTranslate2 branch, after pkg_setup.ensure_runtime() fetches the native runtime on first use.

import paths
import models as model_store
import gpu_detect                    # device routing (auto|cuda|hip|vulkan|cpu)
import backends                      # transcription backend seam (CTranslate2 today)
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

# --- audio-kind capture (mic/voice vs screenshare/Go-Live) ---
CAP = CFG["capture"]
CAP_VOICE = CAP.get("voice", True)
CAP_SCREEN = CAP.get("screenshare", True)
SCREEN_LABEL = CAP.get("screenshare_label", " (stream)")
SCREEN_DETECT_S = CAP.get("screenshare_detect_s", 18.0)
MAX_STALE_S = CAP.get("max_stale_s", 3.0)   # finalize a mic utterance whose transcript stops changing
KEEPALIVE_S = 2.0                   # ping the overlay this often while a stream is still active
# --- name binding: accumulate evidence so the identity with the most hits wins (anti-blip) ---
NAME_VOTE_CAP = 12.0                # ceiling on accumulated confidence per identity
NAME_SWITCH_MARGIN = 4.0            # a challenger must lead the bound name by this many votes to win
NAME_VOTE_NATIVE = 2.0             # weight of an authoritative ssrc-map confirmation
NAME_VOTE_CORR = 1.0               # weight of a speaking-correlation confirmation
NAME_VOTE_DECAY = 0.5             # per-tick decay of identities that didn't get a vote this tick

# --- silence/hallucination gating (kills "Thank you." on quiet audio) ---
GATE = CFG["gating"]
GATE_DBFS = GATE["min_rms_dbfs"]
USE_VAD = GATE["vad"]
REQUIRE_SPEAKING = GATE.get("require_speaking", True)   # trust Discord's speaking indicator to end utterances
# floor the grace: Discord's indicator gaps during normal speech, so too-short a window chops one
# utterance into several lines. 2 s sits above natural between-sentence pauses.
SPK_GRACE_S = max(2.0, float(GATE.get("speaking_grace_s", 2.5) or 2.5))
NO_SPEECH = GATE["no_speech_threshold"]
MIN_LOGPROB = GATE["min_avg_logprob"]
def emit_progress(stage, label, pct=None, done=False):
    """Emit a structured first-run progress line the desktop wrapper parses to drive its
    download/loading banner. Printed as a sentinel line so the GUI can keep it out of the
    visible console; harmless plain text when run headless."""
    try:
        print("[[VTPROG]]" + json.dumps({"stage": stage, "label": label, "pct": pct, "done": done}),
              flush=True)
    except Exception:
        pass


def _norm(s):
    return s.lower().strip().strip(".!?,…\"' ").strip()
DROP = set(_norm(p) for p in GATE["drop_phrases"])

# --- uncensor: undo Whisper's self-bleeping ---------------------------------
# Whisper is trained on caption/subtitle data that bleeps profanity, so it sometimes emits the
# masked form ("f*****g", "sh*t"). When `uncensor` is on we map those masked tokens back to the
# word they almost certainly are. The word list lives in config.json (`uncensor_words`, shipped as
# a default in config.DEFAULTS), so it reaches every config via the defaults merge yet stays
# hand-editable/deletable - the UI only exposes the on/off switch, never the list.
#
# For each target word we build a per-letter skeleton: every position is either the real letter or
# a mask glyph, the token length is exact, and (enforced in the sub callback) >=1 position is
# actually masked - so clean text and unrelated words can't match (a redacted name won't become a
# swear, and a plainly-typed word is left alone).
UNCENSOR = bool(CFG.get("uncensor", False))
_MASK_CHARS = "*✱•·@#$%!"                        # glyphs Whisper substitutes for bleeped letters
_MASK = "[" + re.escape(_MASK_CHARS) + "]"
_EDGE = r"[\w" + re.escape(_MASK_CHARS) + "]"     # token must not abut another letter/mask glyph


def _compile_uncensor(words):
    """Build (regex, word) rules from a plain word list. Longest words first so a long skeleton
    (motherf***er) wins before a shorter one (f***) can grab a slice of it."""
    rules = []
    for w in words or []:
        w = (w or "").strip()
        if not w:
            continue
        body = "".join("(?:%s|%s)" % (re.escape(c), _MASK) for c in w)
        rules.append((re.compile("(?<!%s)%s(?!%s)" % (_EDGE, body, _EDGE), re.I), w))
    rules.sort(key=lambda t: -len(t[1]))
    return rules


_UNCENSOR_RULES = _compile_uncensor(CFG.get("uncensor_words", []))


def _recase(orig, repl):
    letters = [c for c in orig if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):   # F***ING -> FUCKING
        return repl.upper()
    if letters and letters[0].isupper():                          # F*** -> Fuck
        return repl[:1].upper() + repl[1:]
    return repl


def uncensor_text(s):
    for rx, repl in _UNCENSOR_RULES:
        def _sub(m, r=repl):
            tok = m.group(0)
            if not any(ch in _MASK_CHARS for ch in tok):   # a plainly-typed word, not bleeped
                return tok
            return _recase(tok, r)
        s = rx.sub(_sub, s)
    return s

# ---------------- audio capture (Frida) ----------------
buffers = collections.defaultdict(bytearray)   # src -> int16 mono 16k bytes
last_frame = collections.defaultdict(float)
announced = {}                                 # src -> bool (placeholder shown this utterance)
interim_at = collections.defaultdict(float)    # src -> last interim transcribe time
src2user = {}                                  # src -> {userId, name, avatar}
src_client = {}                                # src -> client exe name (e.g. 'discordptb.exe')
src_ssrc = {}                                  # src -> int ssrc read from the native ChannelReceive
src_kind = {}                                  # src -> 'voice' | 'stream' (screenshare/Go Live audio)
native_kind = set()                            # srcs whose kind came from the renderer ssrc map (authoritative)
active_since = {}                              # src -> time the current uninterrupted active run began
last_emit = collections.defaultdict(float)     # src -> last time we sent the overlay anything
last_loud = collections.defaultdict(float)     # src -> last time the audio was speech-level (above gate)
last_change = collections.defaultdict(float)   # src -> last time the transcript text actually changed
last_text = {}                                 # src -> last emitted text (to detect a stuck/unchanging run)
last_speaking = {}                             # uid -> last time Discord's indicator showed them speaking
spk_poll = {}                                  # client -> last time a fresh speaking read succeeded
hooked_clients = set()                         # client exe names the Frida hook attached to
hooked_pids = {}                               # live Frida-hooked pid -> client exe name (lower)
frida_sessions = []                            # keep-alive (session, script) refs
cdp_clients = set()                            # client exe names with a live CDP connection
client_scripts = {}                            # client exe name -> live Frida script (for set_ssrcs rpc)
self_gate = {}                                 # client -> bool: capture my mic for this client right now
corr = collections.defaultdict(dict)           # src -> {uid: co-occurrence score} for speaker binding
bind_votes = collections.defaultdict(lambda: collections.defaultdict(float))  # src -> {uid: naming votes}
lock = threading.Lock()
reinject_event = threading.Event()             # set by the relay when the UI asks to re-inject overlays
manual_assign = {}                             # src -> {"userId":..} | {"name":..}: locked manual binding
pending_assigns = collections.deque()          # relay -> mapping_thread queue: {src, userId?, name?, clear?}


def kind_of(src):
    return src_kind.get(src, "voice")


def kind_enabled(src):
    return CAP_SCREEN if kind_of(src) == "stream" else CAP_VOICE


def display_name(src, name):
    base = name or ("user " + src[-5:])
    return (base + SCREEN_LABEL) if kind_of(src) == "stream" else base

SELF = CFG.get("self_transcribe", {})          # own-voice transcription options


def apply_live_config(cfg):
    """Apply settings that don't need an engine restart, pushed live from the UI over the relay
    control bus. Reassigns the module-level knobs the loops read each pass. Model/device/compute/
    relay-port are deliberately NOT touched here — those still require a restart."""
    global CAP, CAP_VOICE, CAP_SCREEN, SCREEN_DETECT_S, MAX_STALE_S, SCREEN_LABEL
    global GATE, GATE_DBFS, USE_VAD, NO_SPEECH, MIN_LOGPROB, DROP, REQUIRE_SPEAKING, SPK_GRACE_S
    global LANGUAGE, BEAM, SELF, UNCENSOR, _UNCENSOR_RULES
    try:
        if "voice_events" in cfg:
            CFG["voice_events"] = bool(cfg["voice_events"])   # mapping_thread reads this each pass
        if "uncensor" in cfg:
            UNCENSOR = bool(cfg["uncensor"])                  # transcribe() reads this each segment
        if "uncensor_words" in cfg:
            _UNCENSOR_RULES = _compile_uncensor(cfg["uncensor_words"])
        # overlay/alert/inject prefs: keep CFG fresh so the next overlay re-inject uses them
        for k in ("overlay", "alerts", "inject_overlay"):
            if k in cfg:
                CFG[k] = cfg[k]
        CAP = cfg.get("capture") or CAP
        CAP_VOICE = CAP.get("voice", True); CAP_SCREEN = CAP.get("screenshare", True)
        SCREEN_LABEL = CAP.get("screenshare_label", SCREEN_LABEL)
        SCREEN_DETECT_S = CAP.get("screenshare_detect_s", 18.0); MAX_STALE_S = CAP.get("max_stale_s", 3.0)
        GATE = cfg.get("gating") or GATE
        GATE_DBFS = GATE["min_rms_dbfs"]; USE_VAD = GATE["vad"]
        NO_SPEECH = GATE["no_speech_threshold"]; MIN_LOGPROB = GATE["min_avg_logprob"]
        DROP = set(_norm(p) for p in GATE.get("drop_phrases", []))
        REQUIRE_SPEAKING = GATE.get("require_speaking", True)
        SPK_GRACE_S = max(2.0, float(GATE.get("speaking_grace_s", 2.5) or 2.5))
        LANGUAGE = (cfg.get("language") or "").strip() or None
        BEAM = int(cfg.get("beam_size", 1))
        SELF = cfg.get("self_transcribe") or SELF
        print("[cfg] live settings applied (no restart)")
    except Exception as e:
        print("[cfg] live apply failed: %s" % e)


DEBUG_BIND = os.environ.get("VT_DEBUG_BIND") == "1"   # verbose speaker-binding correlation log
DEBUG_EVENTS = os.environ.get("VT_DEBUG_EVENTS") == "1"   # verbose voice-event diffing log

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
let SSRC_OFF = -1;          // located offset of remote_ssrc_ inside ChannelReceive (auto-found)
let WANT = null;           // {ssrc:1} set of valid audio ssrcs, used to locate SSRC_OFF
function modpath() {
  MOD = Process.enumerateModules().find(m => /discord_voice/i.test(m.name)) || null;
  return MOD ? MOD.path : null;
}
function setSsrcs(arr) {    // renderer-supplied audio ssrcs; lets us pin remote_ssrc_'s offset
  const w = {};
  for (const x of arr) w[x >>> 0] = 1;
  WANT = w;
  return true;
}
function readSsrc(recv) {   // recv = ChannelReceive*; find remote_ssrc_ once, then read it cheaply
  if (SSRC_OFF >= 0) { try { return recv.add(SSRC_OFF).readU32(); } catch (e) { return 0; } }
  if (!WANT) return 0;
  for (let o = 0; o < 0x400; o += 4) {
    let v; try { v = recv.add(o).readU32(); } catch (e) { break; }
    if (WANT[v >>> 0]) { SSRC_OFF = o; return v; }
  }
  return 0;
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
      // tag by client (multi-client safe) and by ssrc (native per-stream identity)
      send({ src: PID + ':' + this.src.toString(), client: CLIENT, ssrc: readSsrc(this.src) }, buf);
    }
  });
  send({ ready: true, pid: PID, client: CLIENT });
  return true;
}
rpc.exports = { modpath: modpath, install: install, setSsrcs: setSsrcs };
"""

def on_message(msg, data):
    if msg.get("type") != "send":
        print("[frida]", msg.get("description") or msg); return
    p = msg["payload"]
    if p.get("ready"):
        print("[capture] hook installed (pid %s)" % p.get("pid")); return
    src = p.get("src")
    if src and data:
        now = time.time()
        with lock:
            if now - last_frame.get(src, 0) >= SILENCE_S or src not in active_since:
                active_since[src] = now            # start of a fresh uninterrupted active run
            buffers[src].extend(data)
            last_frame[src] = now
            if p.get("client"):
                src_client[src] = p["client"]
            if p.get("ssrc"):
                src_ssrc[src] = p["ssrc"]

def _on_unhook(pid):
    """A hooked process exited (e.g. a client relaunched from the UI). Forget it so the scan loop
    re-attaches the replacement, and clear its client name if no other process of that name remains."""
    name = hooked_pids.pop(pid, None)
    if name and name not in hooked_pids.values():
        hooked_clients.discard(name)
        client_scripts.pop(name, None)
    if name:
        print("[capture] unhooked PID %d (%s) - will re-attach when it returns" % (pid, name))


def attach_new():
    """Hook the discord_voice node in any Discord-family process we haven't hooked yet. Re-runnable:
    a client relaunched from the UI (new PID) is picked up on the next pass, so it auto-attaches
    without restarting the engine. Skips processes whose discord_voice isn't loaded yet (not in a
    call), and self-prunes via the 'detached' signal."""
    try:
        procs = frida.get_local_device().enumerate_processes()
    except Exception:
        return 0
    n = 0
    for pr in procs:
        if pr.pid in hooked_pids or not re.search(r"discord", pr.name, re.I):
            continue
        try:
            s = frida.attach(pr.pid)
            sc = s.create_script(JS)
            sc.on("message", on_message)
            sc.load()
            path = sc.exports_sync.modpath()
            if not path:                       # discord_voice not loaded yet (no call) - try again later
                s.detach(); continue
            rva, _ = locate_rva(path)
            if not sc.exports_sync.install(rva):
                s.detach(); continue
            name = pr.name.lower()
            hooked_pids[pr.pid] = name
            hooked_clients.add(name)
            client_scripts[name] = sc          # mapping_thread pushes ssrcs here (latest script wins)
            frida_sessions.append((s, sc))
            s.on("detached", lambda *a, _pid=pr.pid: _on_unhook(_pid))
            print("[capture] hooked %s PID %d  RVA 0x%x" % (pr.name, pr.pid, rva))
            n += 1
        except Exception:
            continue
    return n


def attach_thread():
    """Initial hook, then keep polling so a client relaunched from the UI auto-attaches the voice
    node (and the overlay re-injects via the CDP re-probe in mapping_thread)."""
    if not attach_new():
        print("[capture] no discord_voice process yet - will keep watching "
              "(launch a client and join a voice call)")
    while True:
        time.sleep(3)
        try:
            attach_new()
        except Exception:
            pass

# ---------------- own-voice capture (microphone, gated by Discord state) ----------------
def self_capture_thread():
    """Capture the local mic and route it to each client whose self-gate is open (set by
    mapping_thread from Discord's speaking/mute state). Keyed 'self:<client>'. Runs always and
    opens/closes the mic from the live SELF config, so enabling own-voice or switching the input
    device applies without an engine restart."""
    try:
        import sounddevice as sd
    except Exception as e:
        print("[self] sounddevice unavailable (%s); own-voice disabled" % e)
        return

    def push(b16):
        now = time.time()
        with lock:
            for client, open_ in self_gate.items():
                if open_:
                    key = "self:" + client
                    buffers[key].extend(b16)
                    last_frame[key] = now
                    src_client[key] = client

    def open_mic(dev):
        # 16k preferred; fall back to 48k (downsampled by 3) for devices that won't do 16k
        for sr, factor in ((16000, 1), (48000, 3)):
            cb = ((lambda indata, frames, t, s: push(indata.tobytes())) if factor == 1
                  else (lambda indata, frames, t, s: push(np.ascontiguousarray(indata[::factor, 0]).tobytes())))
            try:
                st = sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                    blocksize=int(sr * 0.1), device=dev, callback=cb)
                st.start()
                print("[self] mic capture @%dHz (device=%s)" % (sr, dev if dev is not None else "default"))
                return st
            except Exception as e:
                print("[self] mic open @%dHz failed: %s" % (sr, e))
        return None

    stream = None
    open_dev = object()        # sentinel: nothing opened yet (distinct from device=None "default")
    while True:
        want = bool(SELF.get("enabled"))
        dev = SELF.get("device")
        if want and (stream is None or dev != open_dev):
            if stream is not None:
                try: stream.stop(); stream.close()
                except Exception: pass
                stream = None
            stream = open_mic(dev)
            open_dev = dev if stream is not None else object()    # retry next loop if it failed
        elif not want and stream is not None:
            try: stream.stop(); stream.close()
            except Exception: pass
            stream = None; open_dev = object()
            print("[self] mic capture stopped")
        time.sleep(0.5)


# ---------------- relay (WebSocket) ----------------
clients = set()
relay_loop = None

def start_relay():
    global relay_loop
    async def handler(ws):
        clients.add(ws)
        print("[relay] overlay connected")
        try:
            # the relay is also a tiny control bus: the desktop UI sends control messages
            # (e.g. live keyword edits) and we fan them out to every overlay so highlighting
            # updates without an engine restart.
            async for msg in ws:
                try:
                    obj = json.loads(msg)
                except Exception:
                    continue
                if obj.get("type") == "setKeywords":
                    kws = [str(k) for k in (obj.get("keywords") or [])]
                    broadcast({"type": "keywords", "keywords": kws})
                elif obj.get("type") == "setConfig":
                    apply_live_config(obj.get("config") or {})    # live-apply restart-free settings
                elif obj.get("type") == "reinjectOverlay":
                    reinject_event.set()                          # re-inject overlays without an engine restart
                elif obj.get("type") == "assign":                 # manual speaker (re)assignment, applied by mapping_thread
                    pending_assigns.append({k: obj.get(k) for k in ("src", "userId", "name", "clear")})
        except Exception:
            pass
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


def _client_from_url(url):
    """Map a renderer URL to its client exe, so custom CDP ports still isolate per client.
    Check PTB/Canary before stable, since their hosts contain 'discord.com' as a substring."""
    u = (url or "").lower()
    if "ptb.discord.com" in u:
        return "discordptb.exe"
    if "canary.discord.com" in u:
        return "discordcanary.exe"
    if "discord.com" in u:
        return "discord.exe"
    return None


def mapping_thread():
    from cdp import CDP, speaking_users, user_info, self_state, voice_states, ssrc_map, cleanup_overlay
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
            client = port2client.get(port) or _client_from_url(getattr(c, "url", None))
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
        # Re-resolve when we have nothing OR the cached entry has no avatar: a user first seen "cold"
        # (e.g. the instant they join, before their avatar loads) would otherwise be cached avatarless
        # forever, so every later event for them shows no picture.
        if not info or not info.get("avatar"):
            try:
                fresh = user_info(c, uid)           # resolve via the SAME client
            except Exception:
                fresh = None
            if fresh and fresh.get("avatar"):
                user_cache[uid] = fresh; save_user_cache(); info = fresh
            elif fresh and not info:
                info = fresh                        # keep the name now; don't cache until an avatar lands
        return info

    def bind(s, uid, client, c):
        if src2user.get(s, {}).get("userId") == uid:
            return
        info = resolve_user(c, uid)
        if info:
            src2user[s] = info
            nm = display_name(s, info["name"])
            print("[map] %s [%s] -> %s" % (s[-6:], client, nm))
            broadcast({"type": "rename", "userId": s, "name": nm, "avatar": info["avatar"],
                       "client": client, "resolved": True, "locked": s in manual_assign})

    def set_kind(s, kind, client):
        """Change a source's voice/stream kind and re-broadcast its label if it's already
        bound, so flipping a known speaker to 'stream' actually adds the (stream) suffix
        in the UI (a plain bind() short-circuits once the userId is unchanged)."""
        if src_kind.get(s, "voice") == kind:
            return
        src_kind[s] = kind
        info = src2user.get(s)
        if info and info.get("userId"):
            broadcast({"type": "rename", "userId": s, "name": display_name(s, info["name"]),
                       "avatar": info.get("avatar"), "client": client,
                       "resolved": True, "locked": s in manual_assign})

    def confirm_bind(s, uid, client, c, weight):
        """Accumulate naming evidence per source and bind to the identity with the MOST hits,
        replacing an existing name only when a challenger clearly leads it. A single blip (one
        stray ssrc-map read or correlation tick) can't flip a name that has been consistent; a
        genuine, sustained change still wins after it overtakes by NAME_SWITCH_MARGIN."""
        if s in manual_assign:                 # the user pinned this source -> auto-detect can't touch it
            return
        v = bind_votes[s]
        v[uid] = min(NAME_VOTE_CAP, v[uid] + weight)
        for u in list(v):                       # decay identities with no support this tick
            if u != uid:
                v[u] -= NAME_VOTE_DECAY
                if v[u] <= 0:
                    del v[u]
        leader = max(v, key=v.get)
        cur = (src2user.get(s) or {}).get("userId")
        if cur == leader:
            return
        if cur is None or v[leader] >= v.get(cur, 0.0) + NAME_SWITCH_MARGIN:
            bind(s, leader, client, c)

    def emit_event(kind, uid, client, c):
        info = resolve_user(c, uid) or {"name": "user " + uid[-4:], "avatar": None}
        if DEBUG_EVENTS:
            print("[event] %s: %s (%s)" % (kind, info.get("name"), client))
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
        if reinject_event.is_set():             # UI asked to re-inject overlays (no engine restart)
            reinject_event.clear()
            for _port, _st in list(conns.items()):
                _cl, _cc = _st["client"], _st["cdp"]
                try:
                    if master_inject and inject_for(_cl):
                        inject_overlay(_cc, _cl); injected.add(_cl)
                    else:
                        cleanup_overlay(_cc); injected.discard(_cl)
                except Exception as e:
                    print("[overlay] reinject failed (%s): %s" % (_cl, e))
            print("[overlay] re-injected on request")
        # apply any pending manual (re)assignments queued by the relay
        while pending_assigns:
            try:
                req = pending_assigns.popleft()
            except IndexError:
                break
            src = req.get("src")
            if not src:
                continue
            cl = src_client.get(src)
            if req.get("clear"):                          # hand the source back to auto-detect
                manual_assign.pop(src, None); bind_votes.pop(src, None)
                print("[assign] unlocked %s" % src[-6:])
                broadcast({"type": "rename", "userId": src,
                           "name": display_name(src, (src2user.get(src) or {}).get("name")),
                           "avatar": (src2user.get(src) or {}).get("avatar"), "client": cl,
                           "resolved": bool(src2user.get(src)), "locked": False})
                continue
            uid, nm = req.get("userId"), req.get("name")
            if uid:
                info = None
                for _p, _st in list(conns.items()):       # resolve name+avatar via any live client
                    try:
                        info = resolve_user(_st["cdp"], uid)
                    except Exception:
                        info = None
                    if info:
                        break
                info = info or {"userId": uid, "name": "user " + uid[-4:], "avatar": None}
                manual_assign[src] = {"userId": uid}; src2user[src] = info
            elif nm:                                       # free-text label (no real user)
                info = {"userId": "manual:" + src, "name": nm, "avatar": None}
                manual_assign[src] = {"name": nm}; src2user[src] = info
            else:
                continue
            print("[assign] %s -> %s (locked)" % (src[-6:], info["name"]))
            broadcast({"type": "rename", "userId": src, "name": display_name(src, info["name"]),
                       "avatar": info.get("avatar"), "client": cl, "resolved": True, "locked": True})
        # Correlate INDEPENDENTLY per client: a stream from client X can only ever bind
        # to a speaker reported by client X's own CDP. No cross-client leakage.
        for port, st0 in list(conns.items()):
            client, c = st0["client"], st0["cdp"]
            try:
                speaking = set(speaking_users(c))
                st0["fails"] = 0
                # record who Discord says is speaking so the transcription loop can trust the
                # indicator (and note this client gives fresh speaking data, even when nobody speaks)
                if client:
                    spk_poll[client] = now
                for _uid in speaking:
                    last_speaking[_uid] = now
                # push who's speaking to the UI when the set changes (persists until the next change)
                cur_spk = frozenset(speaking)
                if client and cur_spk != st0.get("spk_bcast"):
                    st0["spk_bcast"] = cur_spk
                    broadcast({"type": "speaking", "client": client, "ids": list(speaking)})
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
                    needs_unmute = SELF.get("only_when_unmuted", True)
                    needs_speak = SELF.get("require_discord_speaking", True)
                    ok_mute = (not needs_unmute) or (not sst.get("muted"))
                    ok_speak = (not needs_speak) or bool(sst.get("speaking"))
                    # Per-client gating: only open when THIS client confirms the conditions. If a gate
                    # is required but this client's state isn't reliably readable (DOM-only, call off
                    # screen), fail closed so a muted/PTT background client can't leak the mic.
                    if (needs_unmute or needs_speak) and not sst.get("reliable"):
                        open_ = False
                    else:
                        open_ = bool(ok_mute and ok_speak)
                with lock:
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
                with lock:
                    self_gate[client] = False

            # voice events: diff this client's channel voice-states ~1x/sec
            if CFG.get("voice_events", True) and client and now - st0.get("vs_t", 0) > 1.0:
                st0["vs_t"] = now
                try:
                    cur_vs = voice_states(c)
                except Exception:
                    cur_vs = None
                if DEBUG_EVENTS:
                    print("[vs %s] cur=%s prev=%s none=%d" % (
                        client, (len(cur_vs) if cur_vs is not None else None),
                        (len(st0["vs_prev"]) if st0.get("vs_prev") else st0.get("vs_prev")),
                        st0.get("vs_none", 0)))
                if cur_vs is None:
                    # A transient empty read (a re-render, or the call briefly off screen) must NOT
                    # wipe the baseline, or we'd miss the leave/mute that happened meanwhile. Only
                    # treat a sustained gap as "left the call".
                    st0["vs_none"] = st0.get("vs_none", 0) + 1
                    if st0["vs_none"] >= 5:
                        st0["vs_prev"] = None
                elif st0.get("vs_prev") is None:
                    st0["vs_none"] = 0
                    st0["vs_prev"] = cur_vs           # seed silently (no events on first snapshot)
                else:
                    st0["vs_none"] = 0
                    diff_voice(client, c, st0["vs_prev"], cur_vs)
                    st0["vs_prev"] = cur_vs

            # roster: the call's members (name + avatar) so the UI's reassign picker can list real
            # people instead of asking for user IDs. Fetched regardless of the voice-events setting.
            if client and now - st0.get("roster_t", 0) > 2.0:
                st0["roster_t"] = now
                try:
                    rvs = voice_states(c)
                except Exception:
                    rvs = None
                if rvs:
                    members = []
                    for uid, vst in rvs.items():
                        info = resolve_user(c, uid)
                        if info:
                            members.append({"userId": uid, "name": info["name"], "avatar": info.get("avatar"),
                                            "stream": bool(vst.get("stream")),
                                            "mute": bool(vst.get("mute") or vst.get("selfMute")),
                                            "deaf": bool(vst.get("deaf") or vst.get("selfDeaf")),
                                            "video": bool(vst.get("video"))})
                    broadcast({"type": "roster", "client": client, "members": members})

            # --- native per-stream binding via ssrc (the reliable path) ---
            # Pull this client's ssrc->user table ~1x/sec and feed the audio ssrcs back to the
            # Frida hook so it can pin remote_ssrc_'s offset and tag every frame with its ssrc.
            if client and now - st0.get("ssrc_t", 0) > 1.0:
                st0["ssrc_t"] = now
                try:
                    sm = ssrc_map(c)
                except Exception:
                    sm = None
                if sm and sm.get("map"):
                    st0["ssrc2user"] = sm["map"]
                    sk = client_scripts.get(client)
                    if sk and sm.get("audio"):
                        try:
                            sk.exports_sync.set_ssrcs([int(x) for x in sm["audio"]])
                        except Exception:
                            pass
            ssrc2user = st0.get("ssrc2user") or {}
            streamers = [uid for uid, v in (st0.get("vs_prev") or {}).items() if v.get("stream")]

            with lock:
                active = [s for s, t in last_frame.items()
                          if now - t < 0.4 and src_client.get(s) == client]
                run_len = {s: now - active_since.get(s, now) for s in active}
                ssrcs = {s: src_ssrc.get(s) for s in active}

            # (1) ssrc -> {userId, kind}: the authoritative path. Discord's renderer tells us, per
            #     connection, which ssrc is a mic ('voice', default connection) and which is a Go
            #     Live's screenshare audio ('stream', StreamRTCConnectionStore). Bind straight to
            #     the owner and classify with no guessing.
            for s in active:
                ent = ssrc2user.get(str(ssrcs.get(s) or ""))
                if ent and ent.get("userId"):
                    native_kind.add(s)
                    set_kind(s, "stream" if ent.get("kind") == "stream" else "voice", client)
                    confirm_bind(s, ent["userId"], client, c, NAME_VOTE_NATIVE)
            # (2) fallback ONLY for clients whose renderer stores are unreachable, so (1) gave no
            #     ssrc map: a source that runs continuously past the threshold while someone is
            #     screensharing is screenshare audio, not speech; one with normal speech gaps heals.
            for s in active:
                if s in native_kind:
                    continue
                if streamers and run_len.get(s, 0) >= SCREEN_DETECT_S:
                    if kind_of(s) != "stream":
                        set_kind(s, "stream", client)
                        if len(streamers) == 1:           # unambiguous owner
                            confirm_bind(s, streamers[0], client, c, NAME_VOTE_NATIVE)
                elif kind_of(s) == "stream":
                    set_kind(s, "voice", client)          # heuristic misfire heals after a pause

            # screenshare streams are excluded from speaker correlation (they have no speaking
            # user, so they would otherwise latch onto whoever happens to be talking).
            vactive = [s for s in active if kind_of(s) != "stream"]
            if not client or not speaking or not vactive:  # never bind without a known client
                continue
            active = vactive
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
                # cast a correlation vote only on clear evidence; confirm_bind owns the hysteresis,
                # so a noisy tick adds at most one vote and can't flip a well-established name.
                if bestv >= second * 1.5 + 1 and (bestv >= 2 if solo else bestv >= 4):
                    confirm_bind(s, best, client, c, NAME_VOTE_CORR)
                    bound = (src2user.get(s) or {}).get("userId")
                    if bound:
                        taken[bound] = s          # reserve whoever the source is actually bound to
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
    global DEVICE, COMPUTE
    load_user_cache()
    # Resolve the configured device against real hardware: auto|cuda|hip|vulkan|cpu -> a concrete
    # backend. CUDA is NVIDIA-only, so device=cuda (or auto) on a non-NVIDIA box becomes cpu;
    # hip/vulkan degrade to cpu until the whisper.cpp backend ships.
    DEVICE = gpu_detect.resolve(DEVICE, print)
    if DEVICE != "cuda" and COMPUTE in ("float16", "int8_float16"):   # GPU-only -> CPU-safe default
        COMPUTE = "int8"
    if DEVICE == "cuda":
        try:
            from cuda_setup import ensure_cuda, cuda_present
            if not cuda_present():
                print("[cuda] GPU runtime not found - downloading (~1 GB, first run only)...")
                emit_progress("cuda", "Downloading GPU runtime (first run, ~1 GB)", 0)
                ensure_cuda(print, on_progress=lambda pct, label: emit_progress("cuda", label, pct))
            add_cuda_dlls()
        except Exception as e:
            print("[cuda] setup failed (%s) - GPU may not load" % e)
    backend = None
    if DEVICE in ("hip", "vulkan"):
        # whisper.cpp (GGML) handles AMD/Intel GPUs, downloaded on first use. Cascade hip -> vulkan
        # -> cpu so a missing HIP artifact or load failure lands on working Vulkan, not straight CPU.
        gfx = gpu_detect.amd_gpu()[0]
        chain = ["hip", "vulkan"] if DEVICE == "hip" else ["vulkan"]
        for be in chain:
            try:
                emit_progress("model", "Preparing %s speech runtime…" % be)
                backend = backends.load_whispercpp(
                    be, gfx if be == "hip" else None, MODEL,
                    beam_size=BEAM, language=LANGUAGE, no_speech_threshold=NO_SPEECH,
                    log=print, on_progress=lambda pct, label: emit_progress("model", label, pct))
                DEVICE = be
                print("[whisper] ready (backend=whisper.cpp/%s, gfx=%s)" % (be, gfx if be == "hip" else "-"))
                break
            except Exception as e:
                print("[whisper] %s backend unavailable (%s)" % (be, e))
        if backend is None:
            print("[whisper] GPU backends unavailable - falling back to CPU")
            emit_progress("model", "GPU unavailable - using CPU")
            DEVICE, COMPUTE = "cpu", "int8"

    if backend is None:
        # CTranslate2 path (cuda/cpu): fetch the delegated ct2/onnxruntime runtime (not bundled),
        # then import faster-whisper. AMD/Intel (whisper.cpp) never reaches here, so never fetches it.
        emit_progress("runtime", "Preparing speech runtime…")
        try:
            pkg_setup.ensure_runtime(print, on_progress=lambda pct, label: emit_progress("runtime", label, pct))
        except Exception as e:
            print("[pkg] runtime setup failed (%s) - model may not load" % e)
        from faster_whisper import WhisperModel
        print("[whisper] loading '%s' on %s (%s)..." % (MODEL, DEVICE, COMPUTE))
        # All models live in one app-owned cache. A model already there is loaded with
        # local_files_only so it NEVER re-downloads (switching back and forth is free); only a
        # genuinely missing model is fetched. The banner text reflects which case it is.
        mr = model_store.cache_dir()
        cached = model_store.is_cached(MODEL)
        if cached:
            emit_progress("model", "Loading speech model '%s'" % MODEL)
        else:
            print("[whisper] '%s' not cached - downloading once into %s" % (MODEL, mr))
            emit_progress("model", "Downloading speech model '%s' (first use)" % MODEL)
        def _load(dev, comp, local_only):
            return WhisperModel(MODEL, device=dev, compute_type=comp,
                                download_root=mr, local_files_only=local_only)
        try:
            model = _load(DEVICE, COMPUTE, cached)
        except Exception as e:
            print("[whisper] load failed (%s)" % e)
            model = None
            if cached:                     # cache looked present but was incomplete -> repair, same device
                try:
                    emit_progress("model", "Repairing speech model '%s'" % MODEL)
                    model = _load(DEVICE, COMPUTE, False)
                except Exception as e2:
                    print("[whisper] refetch failed (%s)" % e2)
            if model is None and DEVICE == "cuda":   # GPU still won't load -> degrade to CPU, don't crash
                print("[whisper] CUDA load failed - falling back to CPU")
                emit_progress("model", "GPU load failed - using CPU")
                DEVICE, COMPUTE = "cpu", "int8"
                model = _load("cpu", "int8", False)
            if model is None:
                raise
        backend = backends.CT2Backend(model, beam_size=BEAM, language=LANGUAGE,
                                      use_vad=USE_VAD, no_speech_threshold=NO_SPEECH)
        print("[whisper] ready (lang=%s, beam=%d, backend=ct2/%s)" % (LANGUAGE or "auto", BEAM, DEVICE))
    emit_progress("ready", "Engine ready", 100, done=True)

    threading.Thread(target=start_relay, daemon=True).start()
    threading.Thread(target=mapping_thread, daemon=True).start()
    threading.Thread(target=self_capture_thread, daemon=True).start()
    threading.Thread(target=attach_thread, daemon=True).start()  # hook + auto-reattach restarted clients

    def rms_dbfs(audio):
        if audio.size == 0:
            return -120.0
        r = float(np.sqrt(np.mean(audio * audio)))
        return 20.0 * np.log10(max(r, 1e-7))

    TAIL_BYTES = int(0.4 * 16000) * 2          # ~0.4 s of recent audio to gauge "still speaking"
    def tail_loud(buf):
        tail = buf[-TAIL_BYTES:] if len(buf) > TAIL_BYTES else buf
        if not tail:
            return False
        audio = np.frombuffer(bytes(tail), dtype=np.int16).astype(np.float32) / 32768.0
        return rms_dbfs(audio) >= GATE_DBFS

    def transcribe(b):
        audio = np.frombuffer(bytes(b), dtype=np.int16).astype(np.float32) / 32768.0
        level = rms_dbfs(audio)
        if level < GATE_DBFS:                          # too quiet to be speech -> skip the model
            return ""
        segs = backend.transcribe(audio)
        out = []
        for s in segs:
            txt = s.text.strip()
            if not txt:
                continue
            if UNCENSOR:
                txt = uncensor_text(txt)
            low_conf = (s.no_speech_prob > NO_SPEECH) or (s.avg_logprob < MIN_LOGPROB)
            # known silence-hallucination phrase + (low confidence or near-quiet) -> drop
            if _norm(txt) in DROP and (low_conf or level < GATE_DBFS + 8):
                continue
            out.append(txt)
        return " ".join(out).strip()

    def emit(src, text, final):
        u = src2user.get(src)
        name = display_name(src, u["name"] if u else None)
        avatar = u["avatar"] if u else None
        now = time.time()
        if text and text != last_text.get(src):    # new/changed words -> reset the stuck timer
            last_text[src] = text
            last_change[src] = now
        last_emit[src] = now
        broadcast({"type": "transcript", "userId": src, "name": name, "avatar": avatar,
                   "text": text, "isFinal": final, "client": src_client.get(src),
                   "kind": kind_of(src), "ts": int(time.time() * 1000),
                   "resolved": bool(u), "locked": src in manual_assign})

    def keepalive(src):
        # tell the overlay this speaker is still active even when a chunk transcribed to nothing,
        # so a long utterance doesn't fade at the subtitle timeout mid-sentence.
        last_emit[src] = time.time()
        broadcast({"type": "keepalive", "userId": src, "client": src_client.get(src)})

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
        keepalives = []
        with lock:
            for src, b in list(buffers.items()):
                if not kind_enabled(src):                      # this kind (voice/screenshare) is toggled off
                    buffers[src] = bytearray(); announced.pop(src, None); interim_at.pop(src, None)
                    continue
                dur = len(b) / 2 / 16000.0
                # Discord-speaking gate: for a MIC source whose user we know, trust Discord's own
                # speaking indicator. Once it reads "not speaking" — yet audio keeps arriving
                # (screenshare bleed, comfort noise, music) — stop treating it as live speech so the
                # utterance can end instead of transcribing forever. Only applied when we have a fresh
                # speaking read AND have actually seen this user speak, so flaky/unsupported clients
                # fall back to the loudness gate alone. Screenshare ('stream') sources have no speaker.
                uid = (src2user.get(src) or {}).get("userId")
                cl = src_client.get(src)
                spk_known = bool(REQUIRE_SPEAKING and kind_of(src) != "stream" and uid and cl
                                 and now - spk_poll.get(cl, 0) < 2.0 and uid in last_speaking)
                spk_silent = spk_known and (now - last_speaking.get(uid, 0) >= SPK_GRACE_S)
                # "Still speaking" is decided by LOUDNESS, not by frames merely arriving: the
                # native stream keeps delivering quiet comfort-noise frames between words, which
                # must neither extend an utterance nor keep a subtitle alive. An utterance ends
                # after SILENCE_S with no speech-level audio, even while quiet frames trickle in.
                if tail_loud(b) and not spk_silent:
                    last_loud[src] = now
                silent = now - last_loud.get(src, 0) >= SILENCE_S
                # Stuck guard: audio keeps coming but the transcript stopped changing (Whisper looping
                # on the same partial). Cut the mic utterance loose so its subtitle can finally expire.
                stale = bool(kind_of(src) != "stream" and announced.get(src)
                             and now - last_change.get(src, now) >= MAX_STALE_S)
                if silent or stale:
                    if stale and not silent:
                        jobs.append((src, last_text.get(src, ""), "stale"))  # finalize last partial, no re-transcribe
                    elif dur >= MIN_UTT_S:
                        jobs.append((src, bytes(b), "final"))
                    buffers[src] = bytearray(); announced.pop(src, None)
                    interim_at.pop(src, None); last_loud.pop(src, None)
                    last_change.pop(src, None); last_text.pop(src, None)
                else:
                    job_added = False
                    if not announced.get(src):
                        announced[src] = True
                        last_change[src] = now      # baseline the stuck timer at the utterance's start
                        jobs.append((src, None, "start")); job_added = True   # instant "speaking…" feedback
                    if dur >= MAX_UTT_S:
                        jobs.append((src, bytes(b), "final")); job_added = True   # cap runaway utterances
                        buffers[src] = bytearray(); announced.pop(src, None)
                        interim_at.pop(src, None); last_loud[src] = now   # keep run alive for the next chunk
                        last_change[src] = now
                    elif dur >= MIN_UTT_S and now - interim_at[src] >= INTERIM_EVERY:
                        interim_at[src] = now
                        jobs.append((src, bytes(b), "interim")); job_added = True   # live partial
                    # actively speaking but no text this tick -> keep the subtitle alive (loudness
                    # gate above means quiet noise can't reach here)
                    if not job_added and announced.get(src) and now - last_emit.get(src, 0) >= KEEPALIVE_S:
                        keepalives.append(src)
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
                    print("  [%s] %s" % (display_name(src, (src2user.get(src) or {}).get("name")), t))
                    emit(src, t, True)
            elif kind == "stale":
                # Whisper got stuck; b carries the last partial. Finalize it (or an empty final to
                # clear the overlay) WITHOUT re-running the model on the same wedged audio.
                emit(src, (b or "").strip() if isinstance(b, str) else "", True)
        for src in keepalives:
            keepalive(src)

if __name__ == "__main__":
    main()
