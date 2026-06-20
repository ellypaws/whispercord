"""First-run GPU runtime setup.

The packaged app does NOT bundle the multi-GB CUDA libraries (cuBLAS/cuDNN). Instead,
on first launch with device=cuda, this downloads just the Windows DLLs from the
nvidia-* PyPI wheels into a local `cuda/` folder next to the app and makes them
loadable. This keeps the distributable small and only pays the download once.
"""
import os, sys, json, glob, zipfile, tempfile, urllib.request

import paths

PKGS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]


def cuda_dir():
    d = paths.data("cuda")
    os.makedirs(d, exist_ok=True)
    return d


def _scan_dirs():
    dirs = [cuda_dir()]
    dirs += glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin"))
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    return dirs


def cuda_present():
    have_blas = have_dnn = False
    for d in _scan_dirs():
        if glob.glob(os.path.join(d, "cublas64*.dll")):
            have_blas = True
        if glob.glob(os.path.join(d, "cudnn*.dll")):
            have_dnn = True
    return have_blas and have_dnn


def _wheel_url(pkg):
    data = json.load(urllib.request.urlopen("https://pypi.org/pypi/%s/json" % pkg, timeout=30))
    cands = [u for u in data["urls"]
             if u["packagetype"] == "bdist_wheel" and "win_amd64" in u["filename"]]
    if not cands:
        raise RuntimeError("no win_amd64 wheel for " + pkg)
    return cands[0]["url"], cands[0]["filename"]


def _download(url, dest, log):
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0))
        got, last = 0, -1
        with open(dest, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); got += len(b)
                if total:
                    pct = got * 100 // total
                    if pct // 10 != last // 10:
                        last = pct; log("[cuda]   %d%% (%d/%d MB)" % (pct, got >> 20, total >> 20))


def ensure_cuda(log=print):
    """Download + extract the CUDA DLLs if missing. Returns True when usable."""
    if cuda_present():
        return True
    dest = cuda_dir()
    for pkg in PKGS:
        url, fn = _wheel_url(pkg)
        log("[cuda] downloading %s ..." % fn)
        tmp = os.path.join(tempfile.gettempdir(), fn)
        _download(url, tmp, log)
        log("[cuda] extracting %s ..." % fn)
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                norm = n.replace("\\", "/")
                if norm.lower().endswith(".dll") and "/bin/" in norm:
                    with open(os.path.join(dest, os.path.basename(n)), "wb") as f:
                        f.write(z.read(n))
        try:
            os.remove(tmp)
        except Exception:
            pass
    log("[cuda] GPU runtime ready in %s" % dest)
    return cuda_present()


if __name__ == "__main__":
    print("present:", cuda_present(), "| dir:", cuda_dir())
