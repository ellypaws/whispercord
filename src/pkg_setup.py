"""First-run native-runtime setup (CTranslate2 + onnxruntime) for the CTranslate2 backend.

These two native wheels dominate the packaged size, so they are intentionally NOT bundled. On
first CTranslate2 use (cuda/cpu) they're downloaded once into a local ``pkgs/`` folder and made
importable, mirroring ``cuda_setup.py``. Same cache philosophy: nothing is re-downloaded if the
package is already importable (a dev venv, or a previous run). An AMD/Intel (whisper.cpp) user
never imports faster-whisper, so they never fetch these at all.

Pinned to the versions the app is built/tested against so the downloaded runtime stays ABI-
compatible with the bundled faster-whisper and the (separately downloaded) CUDA libraries.
"""
import os, sys, json, glob, zipfile, tempfile, importlib, importlib.util, urllib.request

import paths
from cuda_setup import _download          # reuse the chunked, progress-reporting downloader

# pip name -> (import name, pinned version). Pins match the build venv (ct2 4.8.0 / ort 1.23.2).
PKGS = [
    ("ctranslate2", "ctranslate2", "4.8.0"),
    ("onnxruntime", "onnxruntime", "1.23.2"),
]


def pkgs_dir():
    d = paths.data("pkgs")
    os.makedirs(d, exist_ok=True)
    return d


def _add_dll_dirs():
    """Native libs ship inside the extracted wheels (e.g. ctranslate2.libs, onnxruntime/capi)."""
    root = pkgs_dir()
    for sub in [root] + glob.glob(os.path.join(root, "*")) + glob.glob(os.path.join(root, "*", "*")):
        if os.path.isdir(sub):
            try:
                os.add_dll_directory(sub)
            except Exception:
                pass


def present(import_name):
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        return False


def prepare():
    """No-network startup step: make already-downloaded packages importable, and fall back to the
    bundled ``av`` stub when PyAV isn't installed (it's unbundled; faster-whisper imports it at load
    but this app never exercises its file-decode path)."""
    d = pkgs_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    _add_dll_dirs()
    importlib.invalidate_caches()
    if not present("av"):
        stub = paths.resource("_stubs")
        if stub not in sys.path:
            sys.path.append(stub)        # appended: a real PyAV install always wins


def _wheel_url(pkg, ver):
    data = json.load(urllib.request.urlopen("https://pypi.org/pypi/%s/%s/json" % (pkg, ver), timeout=30))
    tag = "cp%d%d" % (sys.version_info[0], sys.version_info[1])
    cands = [u for u in data["urls"]
             if u["packagetype"] == "bdist_wheel" and "win_amd64" in u["filename"]
             and (tag in u["filename"] or "-abi3-" in u["filename"] or "-none-" in u["filename"])]
    cands.sort(key=lambda u: (tag not in u["filename"], "abi3" not in u["filename"]))   # exact cpXX first
    if not cands:
        raise RuntimeError("no compatible win_amd64 wheel for %s %s (python %s)" % (pkg, ver, tag))
    return cands[0]["url"], cands[0]["filename"]


def ensure_runtime(log=print, on_progress=None):
    """Download + extract CTranslate2/onnxruntime if missing. Returns True when both import.
    ``on_progress(pct, label)`` drives the first-run banner, exactly like ``ensure_cuda``."""
    prepare()                                   # path first, so present() sees prior extractions
    todo = [(pip, imp, ver) for pip, imp, ver in PKGS if not present(imp)]
    if not todo:
        return True
    dest = pkgs_dir()
    n = len(todo)
    for i, (pip, imp, ver) in enumerate(todo):
        url, fn = _wheel_url(pip, ver)
        label = "Downloading speech runtime %d/%d (%s)" % (i + 1, n, pip)
        if on_progress:
            on_progress(0, label)
        log("[pkg] downloading %s ..." % fn)
        tmp = os.path.join(tempfile.gettempdir(), fn)
        _download(url, tmp, log,
                  on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
        if on_progress:
            on_progress(100, "Installing speech runtime %d/%d" % (i + 1, n))
        log("[pkg] extracting %s ..." % fn)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest)
        try:
            os.remove(tmp)
        except Exception:
            pass
    _add_dll_dirs()
    importlib.invalidate_caches()
    ok = all(present(imp) for _, imp, _ in PKGS)
    log("[pkg] speech runtime %s in %s" % ("ready" if ok else "INCOMPLETE", dest))
    return ok


if __name__ == "__main__":
    for _, imp, _ in PKGS:
        print("%-14s present=%s" % (imp, present(imp)))
    print("dir:", pkgs_dir())
