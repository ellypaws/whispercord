"""First-run setup for the whisper.cpp backend (AMD/Intel GPU via Vulkan/HIP).

Mirrors cuda_setup.py / pkg_setup.py: the native whisper.cpp library and the GGML model are NOT
bundled — they're downloaded once into the app's data folder and cached. The GPU library variant
is chosen from the detected backend+arch (gpu_detect), so only the matching artifact is fetched.

Artifact scheme (pinned to whisper.cpp v1.9.1):
  * vulkan : whispercpp-vulkan-x64.zip       (our release; universal AMD/Intel/NVIDIA)   [P3 CI]
  * hip    : whispercpp-hip-<gfx>-x64.zip     (our release; per-arch, e.g. gfx1100)        [P3 CI]
  * cpu    : whisper-bin-x64.zip              (upstream official; used to validate the binding)
GGML models come from the official ggerganov/whisper.cpp HF repo.
"""
import os, glob, zipfile, tempfile

import paths
from cuda_setup import _download          # reuse the chunked, progress-reporting downloader

WCPP_VER = "1.9.1"
OUR_BASE = "https://github.com/ellypaws/whispercord/releases/download/whispercpp-v%s/" % WCPP_VER
UPSTREAM_BASE = "https://github.com/ggml-org/whisper.cpp/releases/download/v%s/" % WCPP_VER
HF_MODEL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


def _root():
    d = paths.data("whispercpp")
    os.makedirs(d, exist_ok=True)
    return d


def models_dir():
    d = os.path.join(_root(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def _variant_key(backend, gfx):
    return "hip-%s" % (gfx or "gfx1100") if backend == "hip" else backend   # vulkan | cpu


def _lib_dir(backend, gfx):
    d = os.path.join(_root(), "lib", _variant_key(backend, gfx))
    os.makedirs(d, exist_ok=True)
    return d


def _asset_url(backend, gfx):
    if backend == "vulkan":
        return OUR_BASE + "whispercpp-vulkan-x64.zip"
    if backend == "hip":
        return OUR_BASE + "whispercpp-hip-%s-x64.zip" % (gfx or "gfx1100")
    return UPSTREAM_BASE + "whisper-bin-x64.zip"        # cpu (validation / parity)


def _find_dll(d):
    hits = glob.glob(os.path.join(d, "**", "whisper.dll"), recursive=True)
    return hits[0] if hits else None


def lib_ready(backend, gfx=None):
    return _find_dll(_lib_dir(backend, gfx)) is not None


def ensure_lib(backend, gfx=None, log=print, on_progress=None):
    """Download+extract the whisper.cpp DLL variant if missing. Returns the whisper.dll path and
    registers its folder so dependent ggml*.dll load. Raises if the artifact can't be obtained."""
    d = _lib_dir(backend, gfx)
    dll = _find_dll(d)
    if not dll:
        url = _asset_url(backend, gfx)
        fn = url.rsplit("/", 1)[-1]
        label = "Downloading %s GPU runtime" % backend
        if on_progress:
            on_progress(0, label)
        log("[wcpp] downloading %s ..." % fn)
        tmp = os.path.join(tempfile.gettempdir(), fn)
        _download(url, tmp, log,
                  on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
        log("[wcpp] extracting %s ..." % fn)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(d)
        try:
            os.remove(tmp)
        except Exception:
            pass
        dll = _find_dll(d)
        if not dll:
            raise RuntimeError("whisper.dll not found inside %s" % fn)
    try:
        os.add_dll_directory(os.path.dirname(dll))
    except Exception:
        pass
    return dll


def _ggml_file(name):
    return "ggml-%s.bin" % str(name)        # full precision, matches the CTranslate2 model names


def model_cached(name):
    p = os.path.join(models_dir(), _ggml_file(name))
    return os.path.exists(p) and os.path.getsize(p) > 1_000_000


def ensure_model(name, log=print, on_progress=None):
    """Download the GGML weights for `name` from the official HF repo if missing; return its path."""
    dest = os.path.join(models_dir(), _ggml_file(name))
    if model_cached(name):
        return dest
    url = HF_MODEL_BASE + _ggml_file(name)
    label = "Downloading speech model '%s' (GGML)" % name
    if on_progress:
        on_progress(0, label)
    log("[wcpp] downloading %s ..." % _ggml_file(name))
    _download(url, dest, log,
              on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
    return dest
