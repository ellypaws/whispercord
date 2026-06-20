# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Discord Live Transcriber desktop wrapper.
#
#   build:   pyinstaller discord-transcriber.spec --noconfirm
#   output:  dist/DiscordTranscriber/DiscordTranscriber.exe   (onedir; see ONEFILE below)
#
# The CUDA libraries (cuBLAS/cuDNN) are NOT bundled — they're downloaded on first run by
# cuda_setup.py into a local cuda/ folder. This keeps the distributable small (~hundreds of
# MB instead of ~4 GB); the GPU runtime download happens once on the target machine.

import os
from PyInstaller.utils.hooks import collect_all

ONEFILE = os.environ.get("VT_ONEFILE", "0") == "1"  # set VT_ONEFILE=1 for a single .exe (slow first launch)

datas, binaries, hiddenimports = [], [], []

# Heavy ML / media packages: grab code, data assets, and native libs (but NOT nvidia CUDA).
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += [x for x in b if "nvidia" not in x[0].lower().replace("\\", "/")]
        hiddenimports += h
    except Exception:
        pass

# GUI: pywebview (+ pythonnet/EdgeChromium) and the tray (pystray + Pillow).
for pkg in ("webview", "pystray", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

hiddenimports += [
    "frida", "_frida",
    "websockets", "websockets.sync", "websockets.sync.client",
    "webview.platforms.edgechromium", "webview.platforms.winforms",
    "clr", "clr_loader", "pythonnet",
    "pystray._win32",
    "numpy", "pefile", "capstone",
]

# Our own source + resources (entry script lives in src/).
# config.json is intentionally NOT bundled — it is user-writable data created next to the exe
# at runtime; the app falls back to config.py DEFAULTS when it is absent.
datas += [
    ("src/overlay.js", "."),
    ("src/ui", "ui"),
    ("assets", "assets"),
]

a = Analysis(
    ["src/app.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "nvidia"],
    noarchive=False,
)
pyz = PYZ(a.pure)

ICON = "assets/icon.ico"

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="DiscordTranscriber", debug=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False, icon=ICON,
    )
else:
    exe = EXE(
        pyz, a.scripts, [], exclude_binaries=True,
        name="DiscordTranscriber", debug=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False, icon=ICON,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas, strip=False, upx=False, name="DiscordTranscriber",
    )
