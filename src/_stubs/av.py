"""Stub standing in for PyAV (``av``).

faster-whisper imports ``av`` at module load (``faster_whisper/audio.py``) for its file-decode
path, ``decode_audio()``. whispercord only ever feeds raw 16 kHz PCM numpy arrays to the model,
so that path is never taken. Shipping this stub lets ``import faster_whisper`` succeed without
bundling the ~60 MB PyAV/FFmpeg binaries.

``import av`` succeeds; any real attribute access (``av.open``, ``av.audio`` …) raises a clear
error. This stub is only placed on ``sys.path`` by ``pkg_setup.prepare()`` when PyAV is genuinely
absent, so a real install wins.
"""
__version__ = "0.0.0-stub"


def __getattr__(name):     # PEP 562: the import works; touching av.<x> fails loudly
    raise RuntimeError(
        "PyAV is not bundled in this build (whispercord feeds PCM directly; the "
        "av/decode_audio path is unused). Tried to access av.%s" % name)
