"""First-run setup for the sherpa-onnx Parakeet backend.

The sherpa runtime and Parakeet ONNX model are not bundled. They are downloaded
on first Parakeet use into the app data folder, matching the delegated-runtime
model used by CUDA, CTranslate2, and whisper.cpp.
"""
import glob
import html
import importlib
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

import paths
from cuda_setup import _download

SHERPA_ONNX_VERSION = "1.13.3"
SHERPA_ONNX_CUDA_VERSION = SHERPA_ONNX_VERSION + "+cuda12.cudnn9"
CUDA_INDEX_URL = "https://k2-fsa.github.io/sherpa/onnx/cuda.html"

PARAKEET_MODEL_DEFAULT = "parakeet-tdt-0.6b-v3-int8"
_PARAKEET_ARCHIVE = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
_PARAKEET_RELEASE_TAG = "parakeet-tdt-0.6b-v3-int8"
_OUR_BASE = "https://github.com/ellypaws/whispercord/releases/download/%s/" % _PARAKEET_RELEASE_TAG
_UPSTREAM_PARAKEET_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    + _PARAKEET_ARCHIVE
)

MODEL_ARCHIVES = {
    PARAKEET_MODEL_DEFAULT: {
        "archive": _PARAKEET_ARCHIVE,
        "urls": [_OUR_BASE + _PARAKEET_ARCHIVE, _UPSTREAM_PARAKEET_URL],
    },
}


def _root():
    d = paths.cache("sherpa")
    os.makedirs(d, exist_ok=True)
    return d


def runtime_dir(device):
    d = os.path.join(_root(), "pkgs", "cuda" if device == "cuda" else "cpu")
    os.makedirs(d, exist_ok=True)
    return d


def models_dir():
    d = os.path.join(_root(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def _add_dll_dirs(root):
    for sub in [root] + glob.glob(os.path.join(root, "**"), recursive=True):
        if not os.path.isdir(sub):
            continue
        try:
            if glob.glob(os.path.join(sub, "*.dll")) or glob.glob(os.path.join(sub, "*.pyd")):
                os.add_dll_directory(sub)
        except Exception:
            pass


def _add_cuda_dlls():
    # onnxruntime loads its CUDA provider DLL itself; the provider's transitive deps
    # (cublasLt, cudnn, cufft, cudart) must be on PATH, not merely add_dll_directory'd.
    # Put every candidate CUDA folder on PATH so it works from source venv and frozen alike.
    try:
        import cuda_setup
        for d in cuda_setup.dll_dirs():
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def prepare(device="cpu"):
    """No-network path setup for an already-downloaded sherpa runtime."""
    d = runtime_dir(device)
    if d not in sys.path:
        sys.path.insert(0, d)
    _add_dll_dirs(d)
    if device == "cuda":
        _add_cuda_dlls()
    importlib.invalidate_caches()


def _dist_info(root, prefix):
    return glob.glob(os.path.join(root, "%s-*.dist-info" % prefix))


def _target_ready(device):
    d = runtime_dir(device)
    if not os.path.isdir(os.path.join(d, "sherpa_onnx")):
        return False
    if device == "cuda":
        return any("+cuda12.cudnn9" in os.path.basename(p) for p in _dist_info(d, "sherpa_onnx"))
    return bool(_dist_info(d, "sherpa_onnx")) and bool(_dist_info(d, "sherpa_onnx_core"))


def _wheel_platform():
    if sys.platform == "win32":
        return "win_amd64"
    if sys.platform == "linux":
        return "manylinux2014_x86_64"
    if sys.platform == "darwin":
        return "macosx"
    return None


def _pypi_wheel_url(pkg, ver):
    data = json.load(urllib.request.urlopen("https://pypi.org/pypi/%s/%s/json" % (pkg, ver), timeout=30))
    tag = "cp%d%d" % (sys.version_info[0], sys.version_info[1])
    plat = _wheel_platform()
    cands = []
    for u in data["urls"]:
        fn = u["filename"]
        if u["packagetype"] != "bdist_wheel":
            continue
        if plat and plat not in fn:
            continue
        if tag not in fn and "py3-none" not in fn:
            continue
        cands.append((tag not in fn, "py3-none" not in fn, u["url"], fn))
    cands.sort()
    if not cands:
        raise RuntimeError("no compatible wheel for %s %s (python %s)" % (pkg, ver, tag))
    return cands[0][2], cands[0][3]


def _cuda_wheel_url():
    page = urllib.request.urlopen(CUDA_INDEX_URL, timeout=30).read().decode("utf-8", "replace")
    hrefs = [html.unescape(h) for h in re.findall(r'href=["\']([^"\']+\.whl)["\']', page)]
    tag = "cp%d%d" % (sys.version_info[0], sys.version_info[1])
    plat = _wheel_platform()
    version = SHERPA_ONNX_CUDA_VERSION.replace("+", "%2B")
    cands = []
    for url in hrefs:
        fn = html.unescape(url.rsplit("/", 1)[-1]).replace("%2B", "+")
        if not fn.startswith("sherpa_onnx-%s-" % SHERPA_ONNX_CUDA_VERSION):
            continue
        if tag not in fn:
            continue
        if plat and plat not in fn:
            continue
        cands.append((url, fn))
    if not cands:
        raise RuntimeError("no compatible sherpa-onnx CUDA wheel for %s (python %s)" % (plat or sys.platform, tag))
    # Keep the encoded + in the URL when present. Some hosts require it.
    return cands[0][0].replace(SHERPA_ONNX_CUDA_VERSION, version), cands[0][1]


def _safe_extract_zip(path, dest):
    root = os.path.abspath(dest)
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            target = os.path.abspath(os.path.join(dest, member))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError("unsafe path in wheel: %s" % member)
        z.extractall(dest)


def _download_wheel(url, fn, dest, label, log, on_progress=None):
    if on_progress:
        on_progress(0, label)
    log("[sherpa] downloading %s ..." % fn)
    tmp = os.path.join(tempfile.gettempdir(), fn)
    _download(url, tmp, log,
              on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
    if on_progress:
        # Indeterminate (pct=None) so the bar animates during extraction instead of sitting at 100%.
        on_progress(None, "Extracting %s" % label[len("Downloading "):] if label.startswith("Downloading ") else "Extracting Parakeet runtime")
    log("[sherpa] extracting %s ..." % fn)
    _safe_extract_zip(tmp, dest)
    try:
        os.remove(tmp)
    except Exception:
        pass


def _ensure_cpu_runtime(dest, log, on_progress=None):
    wheels = []
    for pkg in ("sherpa-onnx-core", "sherpa-onnx"):
        url, fn = _pypi_wheel_url(pkg, SHERPA_ONNX_VERSION)
        wheels.append((url, fn))
    for i, (url, fn) in enumerate(wheels):
        label = "Downloading Parakeet runtime %d/%d" % (i + 1, len(wheels))
        _download_wheel(url, fn, dest, label, log, on_progress=on_progress)


def _ensure_cuda_runtime(dest, log, on_progress=None):
    url, fn = _cuda_wheel_url()
    _download_wheel(url, fn, dest, "Downloading Parakeet CUDA runtime", log, on_progress=on_progress)


def ensure_runtime(device, log=print, on_progress=None):
    """Download + extract sherpa-onnx for cpu or cuda, then import and return it."""
    device = "cuda" if device == "cuda" else "cpu"
    dest = runtime_dir(device)
    prepare(device)

    if device == "cuda":
        try:
            from cuda_setup import ensure_cuda_ort
            ensure_cuda_ort(log, on_progress=on_progress)
            _add_cuda_dlls()
        except Exception as e:
            raise RuntimeError("CUDA runtime setup failed: %s" % e)

    if not _target_ready(device):
        if device == "cuda":
            _ensure_cuda_runtime(dest, log, on_progress=on_progress)
        else:
            _ensure_cpu_runtime(dest, log, on_progress=on_progress)

    prepare(device)
    sys.modules.pop("sherpa_onnx", None)
    try:
        sherpa = importlib.import_module("sherpa_onnx")
    except Exception as e:
        raise RuntimeError("sherpa_onnx import failed: %s" % e)
    ver = getattr(sherpa, "__version__", "")
    if device == "cuda" and "+cuda" not in ver:
        raise RuntimeError("CUDA sherpa runtime did not load (version=%s)" % (ver or "unknown"))
    log("[sherpa] runtime ready in %s" % dest)
    return sherpa


def _safe_extract_tar(path, dest):
    root = os.path.abspath(dest)
    with tarfile.open(path, "r:bz2") as t:
        for member in t.getmembers():
            target = os.path.abspath(os.path.join(dest, member.name))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError("unsafe path in model archive: %s" % member.name)
        t.extractall(dest)


def _find_model_dir(root):
    need = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
    for d, _, _ in os.walk(root):
        if all(os.path.exists(os.path.join(d, n)) for n in need):
            return d
    return None


def _download_archive(urls, archive, dest, log, on_progress=None):
    tmp = os.path.join(tempfile.gettempdir(), archive)
    last_err = None
    for url in urls:
        try:
            label = "Downloading Parakeet model"
            if on_progress:
                on_progress(0, label)
            log("[sherpa] downloading %s ..." % archive)
            _download(url, tmp, log,
                      on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
            if on_progress:
                on_progress(None, "Extracting Parakeet model")
            log("[sherpa] extracting %s ..." % archive)
            _safe_extract_tar(tmp, dest)
            return
        except Exception as e:
            last_err = e
            log("[sherpa] download failed from %s (%s)" % (url, e))
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    raise RuntimeError("Parakeet model download failed: %s" % last_err)


def ensure_model(name=PARAKEET_MODEL_DEFAULT, log=print, on_progress=None):
    """Download + extract the selected Parakeet model archive, returning the model dir."""
    spec = MODEL_ARCHIVES.get(name)
    if not spec:
        raise RuntimeError("unsupported Parakeet model: %s" % name)

    dest = os.path.join(models_dir(), name)
    os.makedirs(dest, exist_ok=True)
    found = _find_model_dir(dest)
    if found:
        return found

    _download_archive(spec["urls"], spec["archive"], dest, log, on_progress=on_progress)
    found = _find_model_dir(dest)
    if not found:
        raise RuntimeError("Parakeet model files not found after extracting %s" % spec["archive"])
    log("[sherpa] model ready in %s" % found)
    return found


if __name__ == "__main__":
    print("runtime dir:", runtime_dir(sys.argv[1] if len(sys.argv) > 1 else "cpu"))
    print("models dir:", models_dir())
