"""Path resolution that works both from source and from a frozen PyInstaller exe.

  resource(*p) -> bundled, read-only assets (overlay.js, ui/) — from _MEIPASS when frozen.
  data(*p)     -> user-writable data (config.json, cache/) — next to the exe, or project root in dev.
"""
import os, sys

_FROZEN = getattr(sys, "frozen", False)


def resource_dir():
    if _FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))           # .../src


def data_dir():
    if _FROZEN:
        return os.path.dirname(sys.executable)                  # next to the .exe
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root


def resource(*p):
    return os.path.join(resource_dir(), *p)


def data(*p):
    return os.path.join(data_dir(), *p)
