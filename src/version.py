"""App version, resolved from a single source of truth: the git tag.

You no longer hand-bump a version anywhere. The release workflow rewrites `_STAMP` below from the
pushed tag right before it builds the exe, so a frozen build carries that exact version. In a dev
checkout `_STAMP` is left at the sentinel and the version is derived from `git describe` instead, so
it's never stale. To cut a release you only create the tag (e.g. `git tag v0.2.6 && git push --tags`).
"""
import os, sys, subprocess

GITHUB_REPO = "ellypaws/whispercord"

_STAMP = "0.0.0-dev"   # release CI rewrites THIS line from the tag; do not bump by hand


def _resolve_version():
    # explicit override wins (handy for one-off local frozen builds)
    env = (os.environ.get("WHISPERCORD_VERSION") or "").strip()
    if env:
        return env.lstrip("vV")
    if _STAMP != "0.0.0-dev":
        return _STAMP                       # a stamped release build
    if not getattr(sys, "frozen", False):   # dev checkout: derive from git so it tracks the tag
        try:
            out = subprocess.check_output(
                ["git", "describe", "--tags", "--always", "--dirty"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL, text=True).strip()
            if out:
                return out.lstrip("vV")
        except Exception:
            pass
    return _STAMP


__version__ = _resolve_version()


def _tuple(s):
    out = []
    for part in str(s).lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest, current=None):
    """True when `latest` is a strictly higher version than `current` (default: this build)."""
    return _tuple(latest) > _tuple(current if current is not None else __version__)
