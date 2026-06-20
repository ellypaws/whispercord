"""Single source of truth for the packaged app version, plus a tiny semver compare.

Keep __version__ in step with the release tag (the CI names the exe from the tag) and
with native-ui/DiscordTranscriber.Native.csproj <Version> when cutting a release.
"""
__version__ = "0.2.2"

GITHUB_REPO = "ellypaws/whispercord"


def _tuple(s):
    out = []
    for part in str(s).lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest, current=__version__):
    """True when `latest` is a strictly higher version than `current`."""
    return _tuple(latest) > _tuple(current)
