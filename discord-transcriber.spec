# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Discord Live Transcriber desktop wrapper.
#
#   build:   pyinstaller discord-transcriber.spec --noconfirm
#   output:  dist/DiscordTranscriber.exe   (always a single self-contained .exe)
#
# This build is ALWAYS onefile: one .exe and nothing else. No files are ever dropped next to
# the exe — all user-writable data (config.json) and downloaded runtimes (CUDA, sherpa, GGML
# models) live in the per-user data dir (see src/paths.py: %APPDATA%\whispercord on Windows).
#
# The CUDA libraries (cuBLAS/cuDNN) are NOT bundled — they're downloaded on first run by
# cuda_setup.py into the per-user cache dir. This keeps the distributable small (~hundreds of
# MB instead of ~4 GB); the GPU runtime download happens once on the target machine.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Heavy ML / media packages: grab code, data assets, and native libs (but NOT nvidia CUDA).
# ctranslate2 + onnxruntime are NOT bundled — downloaded on first CTranslate2 use by pkg_setup.py
# (see excludes). av (PyAV, ~60 MB FFmpeg) is unused at runtime; a stub in src/_stubs satisfies
# faster-whisper's load-time `import av`. The whisper.cpp GPU runtime is delegated too.
for pkg in ("faster_whisper", "tokenizers"):
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
    "frida",     # frida>=17 ships the native _frida.pyd inside the package (no top-level _frida)
    "websockets", "websockets.sync", "websockets.sync.client",
    "webview.platforms.edgechromium", "webview.platforms.winforms",
    "clr", "clr_loader", "pythonnet",
    "pystray._win32",
    "numpy", "pefile", "capstone",
    # device routing + first-run runtime downloaders + backends (whispercpp/pkg lazy-imported)
    "gpu_detect", "backends", "cuda_setup", "pkg_setup", "whispercpp_ffi", "whispercpp_setup",
    "sherpa_setup",
]

# Our own source + resources (entry script lives in src/).
# config.json is intentionally NOT bundled — it is user-writable data created in the per-user
# data dir at runtime (paths.data); the app falls back to config.py DEFAULTS when it is absent.
datas += [
    ("src/overlay.js", "."),
    ("src/ui/dist", "ui/dist"),
    ("src/_stubs", "_stubs"),     # av stub (pkg_setup puts it on sys.path when PyAV is absent)
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
    # Downloaded on first run (pkg_setup.py) or unused, so keep them out of the build:
    #   ctranslate2 / onnxruntime — large native wheels;  av — unused (stubbed);
    #   sympy / mpmath — only pulled by onnxruntime's shape-infer tool, never at runtime.
    excludes=["tkinter", "pytest", "nvidia", "ctranslate2", "onnxruntime", "av", "sympy", "mpmath"],
    noarchive=False,
)
pyz = PYZ(a.pure)

ICON = "assets/icon.ico"

# Always onefile: a single self-contained .exe, never a onedir folder. Everything (binaries +
# bundled datas) is packed into the exe; nothing is emitted alongside it.
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="DiscordTranscriber", debug=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, icon=ICON,
)
