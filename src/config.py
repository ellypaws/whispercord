"""Config loader: defaults <- config.json <- env overrides. Used by the orchestrator and passed to the overlay."""
import os, json
import paths

DEFAULTS = {
    "whisper_model": "small",
    "language": "",                     # "" = auto-detect; e.g. "en", "ja"
    "beam_size": 1,                     # 1 = greedy (fastest); higher = more accurate
    "device": "auto",                   # auto | cuda | hip | vulkan | cpu  (auto detects best)
    "compute_type": "float16",          # float16 (gpu) | int8_float16 | int8 (cpu)
    "transcribe_sounds": True,          # keep emitted non-speech events like [laughs] / ♪ music ♪
    "inject_overlay": True,             # also show subtitles inside Discord (via CDP)
    "relay_port": 8765,
    "cdp_port": 9223,                   # back-compat single port
    "cdp_ports": [9223, 9224, 9225, 9226],  # PTB / Discord / Canary / Dev debug ports (any reachable used)
    "silence_s": 0.6,
    "interim_every_s": 1.0,
    "min_utt_s": 0.4,
    "max_utt_s": 12.0,
    "capture": {                        # which audio stream kinds to transcribe
        "voice": True,                  # users' microphone / voice audio
        "screenshare": True,            # Go Live / screenshare audio (e.g. game, music, video)
        "screenshare_label": " (stream)",   # suffix added to a streamer's name for their stream audio
        "screenshare_detect_s": 18.0,   # heuristic fallback: a stream active this long without a
                                        # silence gap is treated as screenshare (until native SSRC kind known)
        "max_stale_s": 3.0,             # cut a microphone utterance loose if its transcript stops
                                        # changing this long (Whisper stuck looping on the same partial)
    },
    "overlay": {
        "show_subtitles": True,         # bottom-center live subtitle bubbles
        "show_log": True,               # top-right scrollable transcript log panel
        "show_status": True,            # small connection/listening status pill
        "subtitle_timeout_ms": 8000,
        "max_blocks": 6,
        "fade_start_count": 5,
        "min_fade_opacity": 0.25,
        "shrink_quiet_subtitles": False,
        "merge_subtitles": True,        # join a user's consecutive utterances into one subtitle bubble
                                        # (within a length/time grace) so context isn't cut off; the
                                        # transcript log still keeps each utterance as its own line
        "log_width": 360,               # in-Discord transcript log width (px, drag-resizable)
        "log_height": 300,              # in-Discord transcript log height (px, drag-resizable)
        "log_autoscroll": True,
    },
    "voice_events": True,               # emit join/leave/mute/deafen/stream events per user
    "keyword_onboarded": False,         # set once the first-run "alert on your name" popup is dismissed/used
    "uncensor": False,                  # restore profanity Whisper self-bleeps ("f*****g" -> "fucking")
    "uncensor_words": [                 # words to un-bleep when `uncensor` is on; edit/delete here
        "motherfucker", "fucking", "fucker", "fucked", "fuck",
        "bullshit", "shit", "bitch", "dick", "damn",
        "asshole", "ass", "cunt", "pussy", "bastard",
    ],
    "ui": {                             # desktop-wrapper display prefs
        "show_timestamps": True,
        "timestamp_format": "clock",    # clock (HH:MM:SS) | relative (12s ago)
        "newest_at_top": False,         # False = newest at bottom (classic); True = newest pops in on top
    },
    "self_transcribe": {                # transcribe your OWN microphone too
        "enabled": False,
        "only_when_unmuted": True,      # skip while self-muted in Discord
        "require_discord_speaking": True,  # only when Discord's VAD says you're speaking (not just our gate)
        "noise_suppression": True,      # denoise the raw mic (Discord's own NS isn't applied to our capture)
        "device": None,                 # input device index/name, or None = system default
        "clients": {},                  # per-client {exe: bool}; absent = on when enabled
    },
    "alerts": {
        "keywords": [],
        "sound": True,
        "highlight": "#f04747",
    },
    "gating": {
        "min_rms_dbfs": -50.0,          # below this loudness, skip the model entirely (silence)
        "require_speaking": True,       # end a mic utterance once Discord's own speaking indicator
                                        # says the user isn't speaking (kills bleed/comfort-noise loops)
        "speaking_grace_s": 2.5,        # how long the indicator must read "not speaking" before cutting;
                                        # keep well above natural between-sentence pauses to avoid chopping
        "vad": True,                    # Silero VAD strips non-speech regions
        "no_speech_threshold": 0.6,     # segment no_speech_prob above this is suspect
        "min_avg_logprob": -1.0,        # segment avg_logprob below this is suspect
        "drop_phrases": [               # known silence-hallucinations, dropped when quiet/low-conf
            "thank you", "thanks for watching", "thank you for watching",
            "please subscribe", "subscribe", "you", "bye", "so", "okay", "ok",
            "...", "♪", "music",
        ],
    },
}

def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a

def load(path=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    path = path or paths.data("config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                _merge(cfg, json.load(f))
        except Exception as e:
            print("[config] failed to read %s: %s" % (path, e))
    if os.environ.get("WHISPER_MODEL"):
        cfg["whisper_model"] = os.environ["WHISPER_MODEL"]
    if os.environ.get("RELAY_PORT"):
        cfg["relay_port"] = int(os.environ["RELAY_PORT"])
    if os.environ.get("CDP_PORT"):
        cfg["cdp_port"] = int(os.environ["CDP_PORT"])
    if os.environ.get("CDP_PORTS"):
        cfg["cdp_ports"] = [int(p) for p in os.environ["CDP_PORTS"].split(",") if p.strip()]
    # ensure the single cdp_port is always among the probed ports
    cfg.setdefault("cdp_ports", [cfg["cdp_port"]])
    if cfg["cdp_port"] not in cfg["cdp_ports"]:
        cfg["cdp_ports"] = [cfg["cdp_port"]] + cfg["cdp_ports"]
    return cfg
