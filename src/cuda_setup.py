"""First-run GPU runtime setup.

The packaged app does NOT bundle the multi-GB CUDA libraries (cuBLAS/cuDNN). Instead,
on first launch with device=cuda, this downloads just the Windows DLLs from the
nvidia-* PyPI wheels into a local `cuda/` folder next to the app and makes them
loadable. This keeps the distributable small and only pays the download once.
"""
import os, sys, json, glob, zipfile, tempfile, urllib.request

import paths

# faster-whisper / CTranslate2 needs just cuBLAS + cuDNN.
PKGS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
# onnxruntime's CUDA execution provider (the sherpa-onnx Parakeet path) links cuFFT and the
# CUDA runtime on top of cuBLAS + cuDNN. cuBLAS already ships cublasLt; cuRAND is not needed.
ORT_PKGS = ["nvidia-cufft-cu12", "nvidia-cuda-runtime-cu12"]


def cuda_dir():
    d = paths.cache("cuda")
    os.makedirs(d, exist_ok=True)
    return d


def dll_dirs():
    """Every folder that may hold the CUDA DLLs, across source venv, onedir, and onefile."""
    dirs = [cuda_dir()]
    dirs += glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin"))
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def _scan_dirs():
    return dll_dirs()


def _have(pattern):
    return any(glob.glob(os.path.join(d, pattern)) for d in dll_dirs())


def cuda_present():
    return _have("cublas64*.dll") and _have("cudnn*.dll")


def ort_cuda_present():
    """cuBLAS + cuDNN plus the extra libs onnxruntime's CUDA provider needs."""
    return cuda_present() and _have("cufft64*.dll") and _have("cudart64*.dll")


def nvidia_gpu_present():
    """True only when an NVIDIA CUDA device is actually usable. CTranslate2 (the faster-whisper
    backend) supports NVIDIA CUDA ONLY — never AMD/Intel — so this is the authoritative answer to
    "can device=cuda work here". It queries the CUDA driver and needs no cuBLAS/cuDNN, so it's safe
    to call before the runtime download (and returns False on AMD/Intel/CPU-only machines)."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _wheel_url(pkg):
    data = json.load(urllib.request.urlopen("https://pypi.org/pypi/%s/json" % pkg, timeout=30))
    cands = [u for u in data["urls"]
             if u["packagetype"] == "bdist_wheel" and "win_amd64" in u["filename"]]
    if not cands:
        raise RuntimeError("no win_amd64 wheel for " + pkg)
    return cands[0]["url"], cands[0]["filename"]


def _download(url, dest, log, on_progress=None):
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0))
        got, last_log, last_pp = 0, -1, -1
        with open(dest, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); got += len(b)
                if total:
                    pct = got * 100 // total
                    if pct != last_pp:
                        last_pp = pct
                        if on_progress:
                            on_progress(got, total)
                    if pct // 10 != last_log // 10:
                        last_log = pct; log("[cuda]   %d%% (%d/%d MB)" % (pct, got >> 20, total >> 20))


def _install_pkgs(pkgs, log, on_progress=None):
    """Download each wheel and extract its bin/*.dll flat into cuda_dir()."""
    dest = cuda_dir()
    n = len(pkgs)
    for i, pkg in enumerate(pkgs):
        url, fn = _wheel_url(pkg)
        label = "Downloading GPU runtime %d/%d (%s)" % (i + 1, n, pkg)
        if on_progress:
            on_progress(0, label)
        log("[cuda] downloading %s ..." % fn)
        tmp = os.path.join(tempfile.gettempdir(), fn)
        _download(url, tmp, log,
                  on_progress=(lambda got, total: on_progress(got * 100 // total, label)) if on_progress else None)
        if on_progress:
            # Indeterminate so the bar keeps moving while the DLLs unzip, not stuck at 100%.
            on_progress(None, "Extracting GPU runtime %d/%d (%s)" % (i + 1, n, pkg))
        log("[cuda] extracting %s ..." % fn)
        with zipfile.ZipFile(tmp) as z:
            for member in z.namelist():
                norm = member.replace("\\", "/")
                if norm.lower().endswith(".dll") and "/bin/" in norm:
                    with open(os.path.join(dest, os.path.basename(member)), "wb") as f:
                        f.write(z.read(member))
        try:
            os.remove(tmp)
        except Exception:
            pass
    log("[cuda] GPU runtime ready in %s" % dest)


def ensure_cuda(log=print, on_progress=None):
    """Download + extract the cuBLAS/cuDNN DLLs if missing. Returns True when usable.
    on_progress(pct, label) is called during download for a first-run progress UI."""
    if cuda_present():
        return True
    _install_pkgs(PKGS, log, on_progress=on_progress)
    return cuda_present()


def ensure_cuda_ort(log=print, on_progress=None):
    """Ensure every CUDA lib onnxruntime's CUDA provider needs (cuBLAS, cuDNN, cuFFT, cudart).
    Downloads only the wheels whose DLLs are missing. Returns True when usable."""
    if ort_cuda_present():
        return True
    need = []
    if not cuda_present():
        need += PKGS
    if not _have("cufft64*.dll"):
        need.append("nvidia-cufft-cu12")
    if not _have("cudart64*.dll"):
        need.append("nvidia-cuda-runtime-cu12")
    if need:
        _install_pkgs(need, log, on_progress=on_progress)
    return ort_cuda_present()


if __name__ == "__main__":
    print("present:", cuda_present(), "| dir:", cuda_dir())
