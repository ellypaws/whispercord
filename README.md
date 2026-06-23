<p align="center">
  <img src="assets/icon.png" width="110" />
  <br>
  <h1 align="center">whispercord</h1>
</p>

<p align="center">
  Live, <b>per-user</b> speech-to-text for Discord voice calls, a desktop app with
  in-Discord subtitles, running entirely on your own machine.
</p>

<p align="center">
  <a href="https://github.com/ellypaws/whispercord/releases/latest">
    <img alt="Latest release" src="https://img.shields.io/github/v/release/ellypaws/whispercord?label=release&logo=github&color=5865f2">
  </a>
  <a href="https://github.com/ellypaws/whispercord/releases">
    <img alt="Downloads" src="https://img.shields.io/github/downloads/ellypaws/whispercord/total?label=downloads&logo=github&color=5865f2">
  </a>
  <a href="https://github.com/ellypaws/whispercord/actions/workflows/release.yml">
    <img alt="Build" src="https://img.shields.io/github/actions/workflow/status/ellypaws/whispercord/release.yml?logo=githubactions&logoColor=white&label=build">
  </a>
  <a href="https://github.com/ellypaws/whispercord">
    <img alt="Python" src="https://img.shields.io/badge/python-3.10+-3776ab?logo=python&logoColor=white">
  </a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-windows-0078d6?logo=windows&logoColor=white">
  <br>
  <a href="https://github.com/ellypaws/whispercord/graphs/contributors">
    <img alt="Contributors" src="https://img.shields.io/github/contributors/ellypaws/whispercord">
  </a>
  <a href="https://github.com/ellypaws/whispercord/commits/main">
    <img alt="Commit activity" src="https://img.shields.io/github/commit-activity/m/ellypaws/whispercord">
  </a>
  <a href="https://github.com/ellypaws/whispercord/stargazers">
    <img alt="Stars" src="https://img.shields.io/github/stars/ellypaws/whispercord?style=social">
  </a>
</p>

<p align="center">
  <a href="https://github.com/ellypaws/whispercord/releases/latest">
    <img alt="Download for Windows" src="https://img.shields.io/badge/Download%20for%20Windows-5865f2?style=for-the-badge&logo=windows&logoColor=white">
  </a>
</p>

---

whispercord puts a live transcript next to your Discord call. Each speaker gets their own
line — with their Discord display name and avatar — even when several people talk at the same
time. You can read it in the desktop app or as subtitles right inside Discord.

> [!NOTE]
> Windows 10/11 with an **NVIDIA GPU** is recommended (CPU works, just slower). It runs against
> the Discord clients you already have installed — stable, PTB, and Canary — at the same time.

## Features

- **Per-user transcripts** — two people talking at once become two separate lines, each
  attributed to the right person with their name and avatar.
- **Per-client panes** — a separate, independently-scrolling transcript for every Discord
  client you run, with a live "N speaking" indicator per pane.
- **In-Discord subtitles** — an optional overlay shows captions at the bottom of Discord with a
  draggable, resizable log.
- **Your own voice** — optionally transcribe your mic too, gated by Discord's own voice
  detection and mute state so it doesn't pick up room noise.
- **Voice events** — join / leave / mute / deafen / stream-start are shown inline, greyed out.
- **Keyword alerts** — highlight and beep when a word you care about (like your name) is spoken.
- **Smart silence gating** — a loudness gate, voice-activity detection, and a phrase blocklist
  suppress the classic "Thank you." line on near-silence.
- **Local and private** — everything stays on `127.0.0.1`; no audio or text leaves your machine.

## Download

> [!TIP]
> Grab the latest **`DiscordTranscriber.exe`** from Releases and run it — it's a single file,
> nothing to install.

<p>
  <a href="https://github.com/ellypaws/whispercord/releases/latest">
    <img alt="Download for Windows" src="https://img.shields.io/badge/Download%20for%20Windows-5865f2?style=for-the-badge&logo=windows&logoColor=white">
  </a>
</p>

The GPU runtime and the Whisper model download themselves on first use, so the download stays
small. The first **Start** shows a progress bar while these are fetched and the model loads, so
nothing happens silently. CPU-only users can switch the device to `cpu` in **Advanced** settings.

## Quick start (from source)

```bat
setup.bat      :: one-time: creates .venv, installs dependencies, builds the frontend
run.bat        :: launches the desktop app
```

Source runs need the frontend artifacts first. `setup.bat` handles this with
`bun install` and `bun run build`; for manual setup, run those commands in `src\ui`
before `python src\app.py`.

1. **Settings → Discord clients** — click **Launch** / **Restart w/ port** so the client(s) you
   want names for are running with their debug port. Capture works without it; names need it.
2. Join a voice call in Discord.
3. Click **Start**, then watch the **Live** tab and/or the subtitles inside Discord.

> [!IMPORTANT]
> A client already running *without* its debug port can't gain one without a restart — the
> **Restart w/ port** button does this (it briefly closes that client's current call).

## The app

- **Live** — one transcript pane **per Discord client**. Each pane auto-scrolls while pinned,
  pauses when you scroll away, and has a **Jump to latest** button. Newest-on-top is a toggle.
- **Settings** — Whisper model, language, per-client subtitle overlay, silence gating, keyword
  alerts, timestamps, display direction, and **Your voice**. Settings save as you change them;
  engine-level changes apply on **Restart engine**.
- **Console** — the engine's live log.
- **Tray** — the window minimizes to a tray icon (Show / Hide / Quit).

## Configuration (`config.json`)

`config.json` is created on first run in your user data folder
(`%APPDATA%\whispercord` on Windows, `~/.local/share/whispercord` on Linux). Everything is
editable from **Settings**, but here are the keys:

| Key                                  | Meaning                                                                              |
|--------------------------------------|--------------------------------------------------------------------------------------|
| `whisper_model`                      | `tiny` / `base` / `small` / `medium` / `large-v3`. Bigger = more accurate, slower.   |
| `language`                           | Force a language (e.g. `en`), or leave empty to auto-detect.                         |
| `inject_overlay`                     | Show subtitles inside Discord — global or per-client.                                |
| `relay_port`                         | Local WebSocket port (default 8765).                                                 |
| `cdp_ports`                          | Debug ports used for name resolution: PTB 9223, Discord 9224, Canary 9225, Dev 9226. |
| `gating.min_rms_dbfs`                | Below this loudness, audio is skipped. Raise toward −45 to gate harder.              |
| `gating.vad` / `gating.drop_phrases` | Voice-activity detection and the silence-hallucination blocklist.                    |
| `alerts.keywords`                    | Words that trigger a highlight and beep.                                             |
| `ui.newest_at_top`                   | Newest line at the top instead of the bottom.                                        |
| `self_transcribe.*`                  | Transcribe your own mic (with mute / voice-detection gating, per client).            |

> [!TIP]
> Environment overrides: `WHISPER_MODEL`, `RELAY_PORT`, `CDP_PORTS`, `VT_INJECT_OVERLAY=0`.

## Build it yourself

```bat
build.bat   :: single DiscordTranscriber.exe in dist\ (same as what releases ship)
```

`build.bat` runs the frontend build before PyInstaller so `src\ui\dist` and
`src\overlay.js` exist for packaging.

The GPU runtime is **not** bundled — on first **Start** with `device=cuda` the app downloads
cuBLAS/cuDNN once into your user data folder, which keeps the build small. Pushing a `v*` tag
runs the [build workflow](.github/workflows/release.yml) and publishes a release with the exe.

## How it works

| Stage          | What happens                                                                                                                                                                                                                  |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Capture**    | Reads each remote speaker's audio directly from the Discord desktop client — the separate per-user streams it normally mixes into one. The lookup is resolved at runtime, so it keeps working across Discord updates.         |
| **Transcribe** | Each speaker's 16 kHz stream goes to [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your GPU, split into utterances with live partials and gated against silence hallucinations.                              |
| **Identify**   | A local connection to the Discord client resolves who is speaking and their display name + avatar, then matches each audio stream to a person — across multiple clients at once, tracked separately so they never cross over. |
| **Display**    | A local WebSocket feeds both the desktop app (one pane per client) and the in-Discord subtitle overlay.                                                                                                                       |

## Troubleshooting

> [!WARNING]
> **Names show as `user 1a2b3`** — that client has no debug port open, or its call isn't on
> screen. Use **Restart w/ port**, and keep the call visible in the client.

- **No transcripts** — you need to be in a voice call with someone *else* talking. Check the
  Console tab for `hook installed`.
- **CUDA / cuBLAS errors** — the GPU runtime downloads on first **Start**; watch the Console and
  your network, or preinstall with `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`. CPU-only:
  set Device = `cpu` in **Advanced**.
- **Still seeing "Thank you." on silence** — raise `gating.min_rms_dbfs` (e.g. −45) and keep
  `gating.vad` on.

## Privacy

Everything runs locally over `127.0.0.1`. No audio or text is sent off your machine.
