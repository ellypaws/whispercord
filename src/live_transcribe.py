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
import os, sys, re, glob, time, json, threading, collections, asyncio, traceback, socket

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
from locate import locate_rva, locate_bind_rvas, locate_event_rvas  # runtime RVA auto-locators
CFG = _load_config()
ASR_ENGINE = (CFG.get("asr_engine") or "whisper").strip().lower()
MODEL = CFG["whisper_model"]
PARAKEET_MODEL = CFG.get("parakeet_model", "parakeet-tdt-0.6b-v3-int8")
LANGUAGE = (CFG.get("language") or "").strip() or None
BEAM = int(CFG.get("beam_size", 1))
DEVICE = CFG.get("device", "cuda")
COMPUTE = CFG.get("compute_type", "float16")
NUM_THREADS = int(CFG.get("num_threads", 0) or 0)   # 0 = auto / library default
TRANSCRIBE_SOUNDS = bool(CFG.get("transcribe_sounds", True))
SAVE_CLIPS = bool(CFG.get("save_clips", False))   # keep finalized-utterance audio for UI replay (opt-in)
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
KEEPALIVE_S = 0.75                  # keep the overlay's 1s quiet-shrink delay from firing mid-sentence
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

# Whisper emits non-speech markers in brackets. Pure-silence ones ([BLANK_AUDIO], [ Silence ],
# (inaudible)) carry no content -> drop them like an empty result (whisper.cpp/Vulkan emits
# [BLANK_AUDIO] where CT2 stays silent). Genuine sound events ([laughs], [LAUGHTER], (claps),
# *Nyuh*, ♪music♪) are kept verbatim only when transcribe_sounds is on; the UI highlights them.
_BLANK_RE = re.compile(
    r"^[\[\(\*♪\s]*(?:blank[\s_]*audio|silence|silent|inaudible|no[\s_]*speech|pause|"
    r"background[\s_]*noise)[\s_]*[\]\)\*♪.]*$", re.I)
def _is_blank_marker(t):
    return bool(_BLANK_RE.match(t or ""))


_SOUND_MARKER_RE = re.compile(
    r"\s*(?:\[[^\]\r\n]*\]|\([^\)\r\n]*\)|\*[^*\r\n]+\*|♪[^♪\r\n]*♪|♪+)\s*")
_ORPHAN_SEPARATOR_RE = re.compile(r"(^|\s)[,;:.-]+(?=\s|$)")
def _strip_sound_markers(t):
    t = _SOUND_MARKER_RE.sub(" ", t or "")
    t = _ORPHAN_SEPARATOR_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def suppress_noise(x):
    """Lightweight stationary spectral-subtraction denoiser for own-mic audio. Discord applies its
    own noise suppression to the streams we hook from others, but our raw mic capture has none, so
    fan/keyboard/room noise leaks into self-transcription. Estimate a per-bin noise floor from the
    utterance's quietest frames and gently subtract it. numpy-only; conservative over-subtraction +
    a spectral floor avoid the musical-noise artifacts that would otherwise confuse the model."""
    x = np.ascontiguousarray(x, dtype=np.float32)
    n_fft, hop = 512, 128
    if x.size < n_fft * 3:
        return x
    win = np.hanning(n_fft).astype(np.float32)
    nf = 1 + (x.size - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(nf)[:, None]
    frames = x[idx] * win
    S = np.fft.rfft(frames, axis=1)
    mag, phase = np.abs(S), np.angle(S)
    noise = np.percentile(mag, 25, axis=0)          # quiet-frame floor per frequency bin
    clean = mag - 1.5 * noise[None, :]              # gentle over-subtraction
    clean = np.maximum(clean, 0.15 * mag)           # spectral floor: keep speech, suppress musical noise
    rec = np.fft.irfft(clean * np.exp(1j * phase), n=n_fft, axis=1).astype(np.float32) * win
    out = np.zeros(x.size, dtype=np.float32)
    wsum = np.zeros(x.size, dtype=np.float32)
    for i in range(nf):
        s = i * hop
        out[s:s + n_fft] += rec[i]
        wsum[s:s + n_fft] += win * win
    nz = wsum > 1e-6
    out[nz] /= wsum[nz]
    out[~nz] = x[~nz]
    return out

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
native_bind = {}                               # (client, ssrc) -> userId, from the native ConnectUser hook
                                               # (authoritative; works with no webpack/BetterDiscord/CDP)
native_speaking = {}                           # client -> {userId: last_ts} from SetRemoteUserSpeaking hook
native_self = {}                               # client -> {"muted":bool,"deaf":bool} from SetSelf* hooks
src_kind = {}                                  # src -> 'voice' | 'stream' (screenshare/Go Live audio)
native_kind = set()                            # srcs whose kind came from the renderer ssrc map (authoritative)
active_since = {}                              # src -> time the current uninterrupted active run began
last_emit = collections.defaultdict(float)     # src -> last time we sent the overlay anything
last_loud = collections.defaultdict(float)     # src -> last time the audio was speech-level (above gate)
last_change = collections.defaultdict(float)   # src -> last time the transcript text actually changed
last_text = {}                                 # src -> last emitted text (to detect a stuck/unchanging run)
last_speaking = {}                             # (client, uid) -> last time that client's indicator showed them speaking
spk_poll = {}                                  # client -> last time a fresh speaking read succeeded
hooked_clients = set()                         # client exe names the Frida hook attached to
hooked_pids = {}                               # live Frida-hooked pid -> client exe name (lower)
frida_sessions = []                            # keep-alive (session, script) refs
cdp_clients = set()                            # client exe names with a live CDP connection
client_scripts = {}                            # client exe name -> live Frida script (for set_ssrcs rpc)
self_gate = {}                                 # client -> bool: capture my mic for this client right now
overlay_status = {}                            # client -> {state, detail, ts}
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


def set_overlay_status(client, state, detail=""):
    if not client:
        return
    with lock:
        overlay_status[client] = {"state": state, "detail": detail or "", "ts": int(time.time() * 1000)}
    broadcast_status()


def status_payload():
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
        total_mapped = len(src2user)
        overlays = dict(overlay_status)
        hooked = set(hooked_clients)
        cdp = set(cdp_clients)
    clients_status = {}
    for cl in (hooked | cdp | set(per.keys()) | set(overlays.keys())) - {"?"}:
        d = per.get(cl, {"streams": 0, "active": 0, "mapped": 0})
        clients_status[cl] = {"hooked": cl in hooked, "cdp": cl in cdp,
                              "streams": d["streams"], "active": d["active"], "mapped": d["mapped"],
                              "overlay": overlays.get(cl)}
    return {"type": "status", "state": "listening", "active": total_active,
            "mapped": total_mapped, "clients": clients_status}


def broadcast_status():
    broadcast(status_payload())

SELF = CFG.get("self_transcribe", {})          # own-voice transcription options
SELF_SPEAK_GRACE_S = 1.0                       # Discord self-speaking can flicker between VAD polls
CDP_ENABLED = bool(CFG.get("cdp_enabled", False))  # opt-in CDP (auto-names + overlay); else native-only


def apply_live_config(cfg):
    """Apply settings that don't need an engine restart, pushed live from the UI over the relay
    control bus. Reassigns the module-level knobs the loops read each pass. Model/device/compute/
    relay-port are deliberately NOT touched here — those still require a restart."""
    global CAP, CAP_VOICE, CAP_SCREEN, SCREEN_DETECT_S, MAX_STALE_S, SCREEN_LABEL
    global GATE, GATE_DBFS, USE_VAD, NO_SPEECH, MIN_LOGPROB, DROP, REQUIRE_SPEAKING, SPK_GRACE_S
    global LANGUAGE, BEAM, TRANSCRIBE_SOUNDS, SELF, UNCENSOR, _UNCENSOR_RULES, SAVE_CLIPS, CDP_ENABLED
    try:
        if "cdp_enabled" in cfg:
            CDP_ENABLED = bool(cfg["cdp_enabled"])            # mapping_thread reads this each pass
            CFG["cdp_enabled"] = CDP_ENABLED
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
        if "transcribe_sounds" in cfg:
            TRANSCRIBE_SOUNDS = bool(cfg.get("transcribe_sounds", True))
        if "save_clips" in cfg:
            SAVE_CLIPS = bool(cfg.get("save_clips", False))
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

self_rpc_ids = set()                            # my own user ids, learned from Discord RPC (no CDP)

def seed_self_from_rpc():
    """Learn my own account(s) via Discord RPC over the named pipe - no CDP/webpack/restart -
    and cache id->{name,avatar} so own-voice lines are labelled even when the renderer stores
    are unreachable. Returns the set of self user ids."""
    try:
        import discord_rpc
        found = discord_rpc.discover_self()
    except Exception:
        return self_rpc_ids
    changed = False
    names = []
    for s in found:
        uid = s.get("id")
        if not uid:
            continue
        self_rpc_ids.add(uid)
        name = s.get("global_name") or s.get("username")
        for n in (s.get("global_name"), s.get("username")):
            if n and n not in names:
                names.append(n)
        av = s.get("avatar")
        avatar = ("https://cdn.discordapp.com/avatars/%s/%s.png?size=64" % (uid, av)) if av \
            else "https://cdn.discordapp.com/embed/avatars/0.png"
        cur = user_cache.get(uid)
        # only fill when missing/avatarless, so a richer guild-specific entry isn't clobbered
        if name and (not cur or not cur.get("avatar")):
            user_cache[uid] = {"userId": uid, "name": name, "avatar": avatar}
            changed = True
    if changed:
        save_user_cache()
    # surface our own names to the UI for keyword onboarding - works with no CDP / no restart
    if names:
        try:
            broadcast({"type": "selfIdentity", "names": names})
        except Exception:
            pass
    return self_rpc_ids

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
function readStdString(p) {   // MSVC std::string: data inline (SSO) when capacity<16, else heap ptr
  try {
    const cap = p.add(0x18).readU64();
    const len = p.add(0x10).readU64();
    const data = cap.compare(16) < 0 ? p : p.readPointer();
    const n = len.toNumber();
    if (n < 0 || n > 64) return null;
    return data.readUtf8String(n);
  } catch (e) { return null; }
}
function installBind(cu, du) {
  // Authoritative SSRC<->userId straight from discord_voice.node, no renderer needed:
  //   Connection::ConnectUser(std::string userId, uint32 audioSsrc, ...)   [rdx=&userId, r8=ssrc]
  //   Connection::DisconnectUser(const std::string& userId)                [rdx=&userId]
  if (!MOD) modpath();
  if (!MOD) return false;
  const CLIENT = ((Process.enumerateModules()[0] || {}).name || "").toLowerCase();
  if (cu) {
    Interceptor.attach(MOD.base.add(cu), {
      onEnter(a) {
        const uid = readStdString(a[1]); const ssrc = a[2].toUInt32();
        if (uid && ssrc) send({ bind: true, client: CLIENT, userId: uid, ssrc: ssrc });
      }
    });
  }
  if (du) {
    Interceptor.attach(MOD.base.add(du), {
      onEnter(a) { const uid = readStdString(a[1]); if (uid) send({ unbind: true, client: CLIENT, userId: uid }); }
    });
  }
  return true;
}
function installEvents(spk, smute, sdeaf) {
  // Native event sources (no CDP):
  //   SetRemoteUserSpeaking(const std::string& userId, SpeakingStatus status, bool) [rdx=&userId, r8=status]
  //   VoiceConnectionWrapper::SetSelfMute(bool) / SetSelfDeafen(bool)               [rdx=bool]
  if (!MOD) modpath();
  if (!MOD) return false;
  const CLIENT = ((Process.enumerateModules()[0] || {}).name || "").toLowerCase();
  if (spk) {
    Interceptor.attach(MOD.base.add(spk), {
      onEnter(a) {
        const uid = readStdString(a[1]);
        if (uid) send({ speak: true, client: CLIENT, userId: uid, on: a[2].toUInt32() !== 0 });
      }
    });
  }
  if (smute) {
    Interceptor.attach(MOD.base.add(smute), { onEnter(a) { send({ selfmute: true, client: CLIENT, muted: a[1].toUInt32() !== 0 }); } });
  }
  if (sdeaf) {
    Interceptor.attach(MOD.base.add(sdeaf), { onEnter(a) { send({ selfdeaf: true, client: CLIENT, deaf: a[1].toUInt32() !== 0 }); } });
  }
  return true;
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
rpc.exports = { modpath: modpath, install: install, setSsrcs: setSsrcs, installBind: installBind, installEvents: installEvents };
"""

def on_message(msg, data):
    if msg.get("type") != "send":
        print("[frida]", msg.get("description") or msg); return
    p = msg["payload"]
    if p.get("ready"):
        print("[capture] hook installed (pid %s)" % p.get("pid")); return
    if p.get("bind"):                          # native ConnectUser: authoritative ssrc -> userId
        cl, uid, ssrc = (p.get("client") or "").lower(), p.get("userId"), p.get("ssrc")
        if cl and uid and ssrc:
            with lock:
                native_bind[(cl, ssrc)] = uid
        return
    if p.get("unbind"):                        # native DisconnectUser: user left this client's call
        cl, uid = (p.get("client") or "").lower(), p.get("userId")
        with lock:
            for k in [k for k, v in native_bind.items() if k[0] == cl and v == uid]:
                del native_bind[k]
            native_speaking.get(cl, {}).pop(uid, None)
        return
    if p.get("speak"):                         # native SetRemoteUserSpeaking: remote speaking on/off
        cl, uid = (p.get("client") or "").lower(), p.get("userId")
        if cl and uid:
            with lock:
                d = native_speaking.setdefault(cl, {})
                if p.get("on"):
                    d[uid] = time.time()
                else:
                    d.pop(uid, None)
        return
    if p.get("selfmute"):                       # native SetSelfMute
        cl = (p.get("client") or "").lower()
        with lock:
            native_self.setdefault(cl, {})["muted"] = bool(p.get("muted"))
        return
    if p.get("selfdeaf"):                       # native SetSelfDeafen
        cl = (p.get("client") or "").lower()
        with lock:
            native_self.setdefault(cl, {})["deaf"] = bool(p.get("deaf"))
        return
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
        with lock:                              # drop this client's native ssrc bindings + event state
            for k in [k for k in native_bind if k[0] == name]:
                del native_bind[k]
            native_speaking.pop(name, None)
            native_self.pop(name, None)
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
            # Authoritative native ssrc->userId binder (no webpack/BD/CDP). Best-effort: if the
            # ConnectUser symbols can't be located on this build, fall back to the CDP ssrc map.
            cu, du = locate_bind_rvas(path)
            bind_ok = False
            if cu or du:
                try:
                    bind_ok = sc.exports_sync.install_bind(cu or 0, du or 0)
                except Exception:
                    bind_ok = False
            # native event sources (speaking + self mute/deaf) - no CDP needed
            ev = locate_event_rvas(path)
            if any(ev.values()):
                try:
                    sc.exports_sync.install_events(ev["speaking"] or 0, ev["selfmute"] or 0, ev["selfdeaf"] or 0)
                except Exception:
                    pass
            name = pr.name.lower()
            hooked_pids[pr.pid] = name
            hooked_clients.add(name)
            client_scripts[name] = sc          # mapping_thread pushes ssrcs here (latest script wins)
            frida_sessions.append((s, sc))
            s.on("detached", lambda *a, _pid=pr.pid: _on_unhook(_pid))
            print("[capture] hooked %s PID %d  RVA 0x%x  native-bind=%s"
                  % (pr.name, pr.pid, rva, "on" if bind_ok else "off"))
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


# ---------------- replayable clips (opt-in, in-RAM ring buffer) ----------------
# When save_clips is on, the raw audio of each finalized utterance is kept here so the UI can
# replay it ("the subtitle doesn't make sense, let me hear it"). In-RAM only - nothing touches
# disk - and bounded: the oldest clip is dropped past CLIP_MAX, matching the keep-last-N UX.
CLIP_MAX = 200
_clips = collections.OrderedDict()      # clipId -> int16 PCM bytes (16 kHz mono)
_clips_lock = threading.Lock()
_clip_seq = 0

def store_clip(b):
    global _clip_seq
    data = bytes(b)
    if not data:
        return None
    with _clips_lock:
        _clip_seq += 1
        cid = str(_clip_seq)
        _clips[cid] = data
        while len(_clips) > CLIP_MAX:
            _clips.popitem(last=False)
    return cid

def clip_wav(cid):
    """16 kHz mono 16-bit WAV (header + PCM) for a stored clip, or None if evicted/unknown."""
    with _clips_lock:
        pcm = _clips.get(cid)
    if pcm is None:
        return None
    import struct
    n, sr = len(pcm), 16000
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) +
            b"data" + struct.pack("<I", n) + pcm)


# ---------------- relay (WebSocket) ----------------
clients = set()
relay_loop = None
relay_sock = None              # the bound listening socket, handed to websockets.serve
RELAY_PORT_TAG = "[[VTPORT]]"  # engine -> GUI: the port the relay actually bound (may differ from config)


def _port_has_live_relay(port):
    """Does a working engine relay already answer on this port? A successful WebSocket handshake
    proves it's OUR relay (not some unrelated app that merely holds the port), so the caller can
    tell 'another engine instance is running' apart from 'port taken by something else'."""
    try:
        from websockets.sync.client import connect as _ws_connect
    except Exception:
        return False                      # can't prove it's a relay -> treat as not-a-relay
    try:
        c = _ws_connect("ws://127.0.0.1:%d" % port, open_timeout=1.5, close_timeout=1.0)
        c.close()
        return True
    except Exception:
        return False


def setup_relay_socket():
    """Bind the relay's listening socket ONCE, before any Discord hook/overlay injection, resolving
    port conflicts up front (hybrid policy):
      - configured port free            -> bind it (normal case).
      - busy AND a live relay answers    -> another engine is already running: exit, so we never
                                            double-hook Discord (which destabilises the client).
      - busy but nothing speaks the relay-> stale socket or unrelated app: bind a free OS port and
                                            tell the GUI/overlay the new port via RELAY_PORT_TAG.
    Runs in the engine's main thread so a single-instance conflict can sys.exit the whole process
    (the GUI's bounded supervisor then shows a clear message instead of an endless 2s retry loop)."""
    global RELAY_PORT, relay_sock
    preferred = RELAY_PORT
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
    except OSError:
        s.close()
        if _port_has_live_relay(preferred):
            print("[relay] port %d already has a running engine - exiting (only one engine can run "
                  "at a time)." % preferred)
            emit_progress("error", "Another copy of the engine is already running. Close it and try again.",
                          done=True)
            sys.exit(3)
        print("[relay] port %d busy but no engine answered - binding a free port instead" % preferred)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
    relay_sock = s
    RELAY_PORT = s.getsockname()[1]
    print("%s%d" % (RELAY_PORT_TAG, RELAY_PORT), flush=True)   # GUI connects its panel to this port
    print("[relay] ws://127.0.0.1:%d" % RELAY_PORT)


def start_relay():
    global relay_loop, relay_sock
    if relay_sock is None:         # supervised restart after the socket was closed: rebind
        setup_relay_socket()
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
                elif obj.get("type") == "getClip":                # lazily serve a replay clip to the asker only
                    cid = str(obj.get("clipId") or "")
                    wav = clip_wav(cid)
                    if wav is not None:
                        import base64
                        await ws.send(json.dumps({"type": "clip", "clipId": cid,
                                                  "wav": base64.b64encode(wav).decode("ascii")}))
                    else:
                        await ws.send(json.dumps({"type": "clip", "clipId": cid, "wav": None}))
                elif obj.get("type") == "assign":                 # manual speaker (re)assignment, applied by mapping_thread
                    pending_assigns.append({k: obj.get(k) for k in ("src", "userId", "name", "clear")})
        except Exception:
            pass
        finally:
            clients.discard(ws)
    async def main():
        global relay_loop, relay_sock
        relay_loop = asyncio.get_event_loop()
        async with websockets.serve(handler, sock=relay_sock):
            relay_sock = None      # ownership transferred to the server; force a rebind on restart
            print("[relay] listening on ws://127.0.0.1:%d" % RELAY_PORT)
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


def _state_reliable(state, key):
    if not state:
        return False
    value = state.get(key)
    if value is None:
        value = state.get("reliable")
    return bool(value)


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
    cstate = {}                                 # client -> per-client state for native-only clients (no CDP)
    injected = set()                            # clients already injected (avoid re-inject spam)

    def try_connect():
        if not CDP_ENABLED:                     # native-only mode: never open a debug port (no restart)
            return
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
            if not master_inject or not inject_for(client):
                set_overlay_status(client, "disabled")
            elif client not in injected:
                set_overlay_status(client, "attaching")
                try:
                    inject_overlay(c, client); injected.add(client)
                    set_overlay_status(client, "attached")
                    print("[overlay] injected (%s)" % (client or "all"))
                except Exception as e:
                    set_overlay_status(client, "failed", str(e))
                    print("[overlay] inject failed (%s): %s" % (client, e))

    def resolve_user(c, uid):
        info = user_cache.get(uid)
        # Re-resolve when we have nothing OR the cached entry has no avatar: a user first seen "cold"
        # (e.g. the instant they join, before their avatar loads) would otherwise be cached avatarless
        # forever, so every later event for them shows no picture. With no CDP (c is None) we can only
        # serve the cache / label-once entries - names auto-resolve once labelled or via opt-in CDP.
        if (not info or not info.get("avatar")) and c is not None:
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
            src2user[s] = {**info, "userId": uid}
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

    def process_native_client(client, st, now):
        """Per-client work for a hooked client with NO CDP connection: bind + speaking + roster +
        self-gate entirely from the Frida native hooks (ConnectUser/SetRemoteUserSpeaking/SetSelf*).
        Names come from the cache / label-once (no auto-resolve without CDP). No correlation needed -
        native_bind is the authoritative ssrc->userId map."""
        # --- speaking (native) ---
        with lock:
            spk = set((native_speaking.get(client) or {}).keys())
            for u in spk:
                last_speaking[(client, u)] = now
        spk_poll[client] = now
        cur_spk = frozenset(spk)
        if cur_spk != st.get("spk_bcast"):
            st["spk_bcast"] = cur_spk
            broadcast({"type": "speaking", "client": client, "ids": list(spk)})

        # --- own-voice gate (native mute; downstream RMS/VAD handles speech detection) ---
        if self_enabled_for(client):
            nself = native_self.get(client) or {}
            open_ = (not SELF.get("only_when_unmuted", True)) or (not nself.get("muted", False))
            with lock:
                self_gate[client] = open_
            key = "self:" + client
            # Re-assert (not just set-once): if anything bound the local mic to a non-self user,
            # correct it back. self id is authoritative here (RPC), so the mic is always 'me'.
            sid = next(iter(self_rpc_ids), None)
            cur = (src2user.get(key) or {}).get("userId")
            if open_ and sid and key not in manual_assign and cur != sid:
                info = user_cache.get(sid)
                if info:
                    src2user[key] = {**info, "userId": sid}
        else:
            with lock:
                self_gate[client] = False

        # --- native ssrc -> userId binding ---
        with lock:
            active = [s for s, t in last_frame.items()
                      if now - t < 0.4 and src_client.get(s) == client and not s.startswith("self:")]
            ssrcs = {s: src_ssrc.get(s) for s in active}
            native_ssrcs = [k[1] for k in native_bind if k[0] == client]
        sk = client_scripts.get(client)          # feed audio ssrcs so the hook pins remote_ssrc_
        if sk and native_ssrcs and now - st.get("ssrc_t", 0) > 1.0:
            st["ssrc_t"] = now
            try:
                sk.exports_sync.set_ssrcs(native_ssrcs)
            except Exception:
                pass
        for s in active:
            sv = ssrcs.get(s)
            uid = native_bind.get((client, sv)) if sv else None
            if uid:
                native_kind.add(s)
                confirm_bind(s, uid, client, None, NAME_VOTE_NATIVE)

        # --- roster: native participants (label-once names from cache) ~2s ---
        if now - st.get("roster_t", 0) > 2.0:
            st["roster_t"] = now
            with lock:
                parts = sorted({v for k, v in native_bind.items() if k[0] == client})
            members = []
            for uid in parts:
                info = user_cache.get(uid)
                members.append({"userId": uid,
                                "name": (info or {}).get("name") or ("user " + uid[-4:]),
                                "avatar": (info or {}).get("avatar"),
                                "stream": False, "mute": False, "deaf": False, "video": False})
            broadcast({"type": "roster", "client": client, "members": members})

    try_connect()
    if CDP_ENABLED and not conns:
        print("[map] CDP enabled but no debug port yet - retrying every 3s")
    elif not CDP_ENABLED:
        print("[map] native-only mode (no CDP/restart): matching via Frida + self via RPC; "
              "names from cache/label-once. Enable CDP for auto-stranger-names + in-Discord overlay.")

    # self-detection via RPC (no CDP needed). discover_self() blocks ~1-2s probing pipes, so it
    # runs off-thread to never stall the 0.25s mapping loop.
    def _seed_self():
        ids = seed_self_from_rpc()
        if ids:
            print("[rpc] self id(s): %s" % ", ".join(sorted(ids)))
    threading.Thread(target=_seed_self, daemon=True).start()

    last_probe = time.time()
    last_rpc = time.time()
    while True:
        time.sleep(0.25)
        now = time.time()
        if now - last_probe > 3:                # re-probe for newly-opened ports (e.g. you restarted Canary)
            last_probe = now
            try_connect()
        if now - last_rpc > 30:                 # refresh self identity (a client may have (re)launched)
            last_rpc = now
            threading.Thread(target=seed_self_from_rpc, daemon=True).start()
        if reinject_event.is_set():             # UI asked to re-inject overlays (no engine restart)
            reinject_event.clear()
            for _port, _st in list(conns.items()):
                _cl, _cc = _st["client"], _st["cdp"]
                try:
                    if master_inject and inject_for(_cl):
                        set_overlay_status(_cl, "reloading")
                        inject_overlay(_cc, _cl); injected.add(_cl)
                        set_overlay_status(_cl, "attached")
                    else:
                        set_overlay_status(_cl, "disabled")
                        cleanup_overlay(_cc); injected.discard(_cl)
                except Exception as e:
                    set_overlay_status(_cl, "failed", str(e))
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
            # the real discord userId behind this source (for label-once persistence): explicit pick,
            # current binding, or the native ssrc map.
            real_uid = uid or (src2user.get(src) or {}).get("userId")
            if real_uid and str(real_uid).startswith("manual:"):
                real_uid = None
            if not real_uid and cl:
                _sv = src_ssrc.get(src)
                real_uid = native_bind.get((cl, _sv)) if _sv else None
            if uid:
                info = None
                for _p, _st in list(conns.items()):       # resolve name+avatar via any live client
                    try:
                        info = resolve_user(_st["cdp"], uid)
                    except Exception:
                        info = None
                    if info:
                        break
                info = info or user_cache.get(uid) or {"userId": uid, "name": nm or ("user " + uid[-4:]), "avatar": None}
                if nm:
                    info = {**info, "name": nm}
                manual_assign[src] = {"userId": uid}; src2user[src] = {**info, "userId": uid}
                if nm:                                     # label-once: persist this person's name
                    user_cache[uid] = {"userId": uid, "name": nm, "avatar": info.get("avatar")}
                    save_user_cache()
            elif nm and real_uid:                          # free-text name for a natively-known person
                info = {"userId": real_uid, "name": nm, "avatar": (user_cache.get(real_uid) or {}).get("avatar")}
                src2user[src] = info; manual_assign[src] = {"userId": real_uid}
                user_cache[real_uid] = {"userId": real_uid, "name": nm, "avatar": info.get("avatar")}
                save_user_cache()
            elif nm:                                       # free-text label (no real user behind it)
                info = {"userId": "manual:" + src, "name": nm, "avatar": None}
                manual_assign[src] = {"name": nm}; src2user[src] = info
            else:
                continue
            print("[assign] %s -> %s (locked)" % (src[-6:], info["name"]))
            broadcast({"type": "rename", "userId": src, "name": display_name(src, info["name"]),
                       "avatar": info.get("avatar"), "client": cl, "resolved": True, "locked": True})
        # Correlate INDEPENDENTLY per client: a stream from client X can only ever bind
        # to a speaker reported by client X's own CDP. No cross-client leakage.
        for port, st0 in (list(conns.items()) if CDP_ENABLED else []):
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
                        set_overlay_status(client, "failed", "CDP disconnected")
                    injected.discard(client)
                    print("[map] CDP on port %d dropped; will retry" % port)
                continue

            try:
                sst = self_state(c)
            except Exception:
                sst = None

            # record who Discord says is speaking so the transcription loop can trust the
            # indicator (and note this client gives fresh speaking data, even when nobody speaks).
            # speaking_users() intentionally excludes self for binding; self_state() is merged only
            # for UI activity and own-voice gating so the green ring also reflects your local client.
            if client:
                spk_poll[client] = now
                for _uid in speaking:
                    last_speaking[(client, _uid)] = now
            ui_speaking = set(speaking)
            if client and sst and _state_reliable(sst, "speakingReliable") and sst.get("inCall") and sst.get("speaking") and sst.get("selfId"):
                ui_speaking.add(sst["selfId"])
                last_speaking[(client, sst["selfId"])] = now
            # push who's speaking to the UI when the set changes (persists until the next change)
            cur_spk = frozenset(ui_speaking)
            if client and cur_spk != st0.get("spk_bcast"):
                st0["spk_bcast"] = cur_spk
                broadcast({"type": "speaking", "client": client, "ids": list(ui_speaking)})

            # own-voice gate (per client): only capture my mic when Discord agrees I'm speaking/unmuted
            if client and self_enabled_for(client):
                open_ = False
                if sst and sst.get("inCall"):
                    needs_unmute = SELF.get("only_when_unmuted", True)
                    needs_speak = SELF.get("require_discord_speaking", True)
                    mute_reliable = _state_reliable(sst, "muteReliable")
                    speak_reliable = _state_reliable(sst, "speakingReliable")
                    ok_mute = (not needs_unmute) or (not sst.get("muted"))
                    self_spk_key = (client, sst.get("selfId")) if sst.get("selfId") else None
                    ok_speak = (not needs_speak) or bool(
                        self_spk_key and now - last_speaking.get(self_spk_key, 0) <= SELF_SPEAK_GRACE_S)
                    # Per-client gating: only open when THIS client confirms the required conditions.
                    # DOM fallback can still give a reliable self mute switch even if other self-state
                    # fields are not reliable, so fail closed per signal instead of all-or-nothing.
                    if (needs_unmute and not mute_reliable) or (needs_speak and not speak_reliable):
                        open_ = False
                    else:
                        open_ = bool(ok_mute and ok_speak)
                with lock:
                    self_gate[client] = open_
                key = "self:" + client
                sid = sst.get("selfId") if sst else None
                cur = (src2user.get(key) or {}).get("userId")
                # Re-assert: correct the mic back to 'me' if correlation bound it to another user.
                if open_ and sid and key not in manual_assign and cur != sid:
                    info = user_cache.get(sid)
                    if not info:
                        try:
                            info = user_info(c, sid)
                        except Exception:
                            info = None
                    if info:
                        src2user[key] = {**info, "userId": sid}
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

            # self identity: surface our own Discord names (server nick, real display name, username)
            # so the desktop UI can offer them as alert keywords. Broadcast once per change per client.
            if client and now - st0.get("selfid_t", 0) > 4.0:
                st0["selfid_t"] = now
                try:
                    sid = self_state(c)
                except Exception:
                    sid = None
                if sid and sid.get("selfId"):
                    names = [n for n in (sid.get("nick"), sid.get("globalName"), sid.get("username")) if n]
                    if names and names != st0.get("selfid_names"):
                        st0["selfid_names"] = names
                        broadcast({"type": "selfIdentity", "names": names})

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
                # Feed the hook the set of valid audio ssrcs so it can pin remote_ssrc_'s offset and
                # tag every frame. Source it from the CDP map when reachable AND from the native
                # ConnectUser binds - the latter is what makes this work on non-BD/no-webpack clients.
                with lock:
                    native_ssrcs = [k[1] for k in native_bind if k[0] == client]
                cdp_ssrcs = [int(x) for x in (sm.get("audio") or [])] if sm else []
                audio_ssrcs = list(set(cdp_ssrcs) | set(native_ssrcs))
                sk = client_scripts.get(client)
                if sk and audio_ssrcs:
                    try:
                        sk.exports_sync.set_ssrcs(audio_ssrcs)
                    except Exception:
                        pass
            ssrc2user = st0.get("ssrc2user") or {}
            streamers = [uid for uid, v in (st0.get("vs_prev") or {}).items() if v.get("stream")]

            with lock:
                # 'self:<client>' is the local mic; its identity is owned solely by the self-gate.
                # It must never be a correlation/bind candidate, or it latches onto whoever is
                # speaking remotely (binding your own line to another user).
                active = [s for s, t in last_frame.items()
                          if now - t < 0.4 and src_client.get(s) == client and not s.startswith("self:")]
                run_len = {s: now - active_since.get(s, now) for s in active}
                ssrcs = {s: src_ssrc.get(s) for s in active}

            # (1) ssrc -> {userId, kind}: the authoritative path. Two sources, both ground truth:
            #     - native_bind: discord_voice.node's ConnectUser hook (no webpack/BD/CDP needed -
            #       works on vanilla stable/Canary where the renderer stores are unreachable).
            #     - ssrc2user: the renderer ssrc map (when BD/webpack is reachable) - also carries
            #       the voice/stream classification (mic vs Go-Live screenshare audio).
            #     Prefer native_bind for the identity; use ssrc2user for kind, else default 'voice'.
            for s in active:
                sv = ssrcs.get(s)
                ent = ssrc2user.get(str(sv or "")) if sv else None
                uid = native_bind.get((client, sv)) if sv else None
                if not uid and ent:
                    uid = ent.get("userId")
                if uid:
                    native_kind.add(s)
                    set_kind(s, "stream" if (ent and ent.get("kind") == "stream") else "voice", client)
                    confirm_bind(s, uid, client, c, NAME_VOTE_NATIVE)
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

        # Native-only clients: hooked (audio) but with no CDP connection. When CDP is off (the
        # default) this is every hooked client; when on, the ones without a live debug port. Driven
        # entirely by the Frida native hooks - no restart, no webpack, no BetterDiscord.
        with lock:
            cdp_names = {st0["client"] for st0 in conns.values() if st0.get("client")}
            native_only = [cl for cl in hooked_clients if cl and cl not in cdp_names]
        for client in native_only:
            try:
                process_native_client(client, cstate.setdefault(client, {}), now)
            except Exception as e:
                if DEBUG_BIND:
                    print("[native %s] %s" % (client, e))

# ---------------- transcription ----------------
def purge_stale_sources(now, idle_s=600.0):
    """Drop per-source state for streams gone silent a long time. Discord allocates a new
    ChannelReceive (a new src key) whenever someone reconnects/rejoins, so over a multi-hour
    session these maps would grow without bound and slowly leak memory. A purged src that ever
    returns is simply recreated on its next frame; nothing breaks. Manual pins are kept."""
    with lock:
        stale = [s for s, t in list(last_frame.items()) if now - t > idle_s and s not in manual_assign]
        for s in stale:
            for d in (buffers, last_frame, active_since, last_emit, last_loud, last_change,
                      last_text, announced, interim_at, corr, bind_votes, src_ssrc, src_kind,
                      src_client, src2user):
                d.pop(s, None)
            native_kind.discard(s)
    if stale:
        print("[gc] purged %d idle source(s)" % len(stale))


def main():
    global ASR_ENGINE, DEVICE, COMPUTE
    load_user_cache()
    backend = None
    configured_device = DEVICE

    if ASR_ENGINE == "parakeet":
        # Parakeet is offline-only and auto-detects its 25 European languages. language,
        # beam_size, compute_type, and transcribe_sounds do not affect this backend.
        pdev = gpu_detect.resolve_parakeet(configured_device, print)
        tries = [pdev] + (["cpu"] if pdev == "cuda" else [])
        for dev in tries:
            try:
                emit_progress("model", "Preparing Parakeet speech runtime")
                backend = backends.load_sherpa_onnx(
                    PARAKEET_MODEL, dev, num_threads=(NUM_THREADS if NUM_THREADS > 0 else 4),
                    log=print, on_progress=lambda pct, label: emit_progress("model", label, pct))
                DEVICE, COMPUTE = dev, "int8"
                print("[parakeet] ready (model=%s, backend=sherpa/%s, lang=auto-25-eu)"
                      % (PARAKEET_MODEL, dev))
                break
            except Exception as e:
                print("[parakeet] %s backend unavailable (%s)" % (dev, e))
        if backend is None:
            print("[parakeet] unavailable - falling back to Whisper")
            emit_progress("model", "Parakeet unavailable - using Whisper")
            ASR_ENGINE = "whisper"
            DEVICE = configured_device

    if backend is None:
        # Resolve the configured device against real hardware: auto|cuda|hip|vulkan|cpu -> a concrete
        # backend. CUDA is NVIDIA-only, so device=cuda (or auto) on a non-NVIDIA box becomes cpu.
        DEVICE = gpu_detect.resolve(DEVICE, print)
        if DEVICE != "cuda" and COMPUTE in ("float16", "int8_float16"):   # GPU-only -> CPU-safe default
            COMPUTE = "int8"

    if backend is None and DEVICE == "cuda":
        try:
            from cuda_setup import ensure_cuda, cuda_present
            if not cuda_present():
                print("[cuda] GPU runtime not found - downloading (~1 GB, first run only)...")
                emit_progress("cuda", "Downloading GPU runtime (first run, ~1 GB)", 0)
                ensure_cuda(print, on_progress=lambda pct, label: emit_progress("cuda", label, pct))
            add_cuda_dlls()
        except Exception as e:
            print("[cuda] setup failed (%s) - GPU may not load" % e)
    if backend is None and DEVICE in ("hip", "vulkan"):
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
                    transcribe_sounds=TRANSCRIBE_SOUNDS,
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
            return WhisperModel(MODEL, device=dev, compute_type=comp, cpu_threads=NUM_THREADS,
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
                                      use_vad=USE_VAD, no_speech_threshold=NO_SPEECH,
                                      transcribe_sounds=TRANSCRIBE_SOUNDS)
        print("[whisper] ready (lang=%s, beam=%d, backend=ct2/%s)" % (LANGUAGE or "auto", BEAM, DEVICE))
    emit_progress("ready", "Engine ready", 100, done=True)

    # Supervise the worker threads: if one raises, restart it rather than silently losing that
    # subsystem (relay/overlay, speaker mapping, mic capture) for the rest of the session, which
    # would look like the engine "stopping" even though the process is still alive.
    def _supervised(target, name):
        def run():
            while True:
                try:
                    target()
                except Exception:
                    print("[run] thread %r crashed; restarting in 2s:" % name)
                    traceback.print_exc()
                    time.sleep(2.0)
        threading.Thread(target=run, daemon=True, name=name).start()
    setup_relay_socket()    # bind/resolve the port up front (may sys.exit on a single-instance clash)
    _supervised(start_relay, "relay")
    _supervised(mapping_thread, "mapping")
    _supervised(self_capture_thread, "self_capture")
    _supervised(attach_thread, "attach")  # hook + auto-reattach restarted clients

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

    def transcribe(b, denoise=False):
        audio = np.frombuffer(bytes(b), dtype=np.int16).astype(np.float32) / 32768.0
        if denoise and audio.size:                     # own-mic noise suppression (raw mic, no Discord NS)
            audio = suppress_noise(audio)
        level = rms_dbfs(audio)
        if level < GATE_DBFS:                          # too quiet to be speech -> skip the model
            return ""
        segs = backend.transcribe(audio, transcribe_sounds=TRANSCRIBE_SOUNDS)
        out = []
        for s in segs:
            txt = s.text.strip()
            if not txt or _is_blank_marker(txt):       # silence/blank-audio marker -> nothing (like "...")
                continue
            if not TRANSCRIBE_SOUNDS:
                txt = _strip_sound_markers(txt)
                if not txt or _is_blank_marker(txt):
                    continue
            if UNCENSOR:
                txt = uncensor_text(txt)
            low_conf = (s.no_speech_prob > NO_SPEECH) or (s.avg_logprob < MIN_LOGPROB)
            # known silence-hallucination phrase + (low confidence or near-quiet) -> drop
            if _norm(txt) in DROP and (low_conf or level < GATE_DBFS + 8):
                continue
            out.append(txt)
        return " ".join(out).strip()

    def emit(src, text, final, clip_id=None):
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
                   "resolved": bool(u), "locked": src in manual_assign, "clipId": clip_id})

    def keepalive(src):
        # tell the overlay this speaker is still active even when a chunk transcribed to nothing,
        # so a long utterance doesn't fade at the subtitle timeout mid-sentence.
        last_emit[src] = time.time()
        broadcast({"type": "keepalive", "userId": src, "client": src_client.get(src)})

    def heartbeat():
        while True:
            time.sleep(1.5)
            broadcast_status()
    _supervised(heartbeat, "heartbeat")

    def _tick():
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
                spk_key = (cl, uid) if cl and uid else None
                spk_known = bool(REQUIRE_SPEAKING and kind_of(src) != "stream" and spk_key
                                 and now - spk_poll.get(cl, 0) < 2.0 and spk_key in last_speaking)
                spk_silent = spk_known and (now - last_speaking.get(spk_key, 0) >= SPK_GRACE_S)
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
                denoise = src.startswith("self:") and bool(SELF.get("noise_suppression", True))
                t = transcribe(b, denoise=denoise)
                if t:
                    emit(src, t, False)
            elif kind == "final":
                denoise = src.startswith("self:") and bool(SELF.get("noise_suppression", True))
                t = transcribe(b, denoise=denoise)
                clip_id = None
                if t:
                    print("  [%s] %s" % (display_name(src, (src2user.get(src) or {}).get("name")), t))
                    if SAVE_CLIPS:
                        clip_id = store_clip(b)        # keep the audio behind this line for UI replay
                emit(src, t, True, clip_id)
            elif kind == "stale":
                # Whisper got stuck; b carries the last partial. Finalize it (or an empty final to
                # clear the overlay) WITHOUT re-running the model on the same wedged audio.
                emit(src, (b or "").strip() if isinstance(b, str) else "", True)
        for src in keepalives:
            keepalive(src)

    print("[run] live transcription running (Ctrl+C to stop)")
    last_purge = time.time()
    while True:
        time.sleep(0.2)
        # A single bad chunk (transient CUDA error, odd buffer) must never kill the whole engine:
        # log it and keep going. Hard crashes the process can't catch are restarted by the wrapper.
        try:
            _tick()
        except Exception:
            print("[run] transcription tick failed (continuing):")
            traceback.print_exc()
        now = time.time()
        if now - last_purge > 60.0:
            last_purge = now
            try:
                purge_stale_sources(now)
            except Exception:
                pass

if __name__ == "__main__":
    main()
