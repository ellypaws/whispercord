"""Path resolution that works from source, from a onedir build, and from a single-file exe.

  resource(*p) -> bundled, read-only assets (overlay.js, ui/) — from _MEIPASS when frozen.
  data(*p)     -> user-writable data (config.json, cache/, cuda/).

A single-file exe can't reliably write next to itself (it runs from a temp extraction, and may
live in a read-only location), so frozen builds store data in a per-user directory:
  Windows  %APPDATA%\\whispercord
  macOS    ~/Library/Application Support/whispercord
  Linux    $XDG_DATA_HOME/whispercord  (default ~/.local/share/whispercord)
From source we keep everything in the project root for easy iteration.
"""
import os, sys

_FROZEN = getattr(sys, "frozen", False)
_APP = "whispercord"


def resource_dir():
    if _FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))           # .../src


def _user_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:  # linux / other unix
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, _APP)


def data_dir():
    d = _user_data_dir() if _FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def cache_dir():
    """Where large downloaded runtimes/models live (CUDA libs, sherpa-onnx, GGML models).

    Always the per-user folder, even from source: these are hundreds of MB and must never
    land next to the exe or in the repo. config.json stays in data_dir() for easy iteration;
    only the heavy delegated runtimes go here. When frozen this equals data_dir(), so existing
    installs keep the same paths.
    """
    d = _user_data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def resource(*p):
    return os.path.join(resource_dir(), *p)


def data(*p):
    return os.path.join(data_dir(), *p)


def cache(*p):
    return os.path.join(cache_dir(), *p)
