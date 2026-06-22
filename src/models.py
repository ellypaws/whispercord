"""Downloaded-model cache management, across every engine.

Three engines keep their weights in three different places:
  * Whisper / NVIDIA+CPU (faster-whisper / CTranslate2) -> the standard Hugging Face hub cache
  * Whisper / AMD+Intel  (whisper.cpp GGML)             -> whispercpp_setup.models_dir()
  * Parakeet             (sherpa-onnx)                  -> sherpa_setup.models_dir()

For CTranslate2 we operate on the SAME HF cache faster-whisper uses (not a private folder) so
nothing is ever re-downloaded because of this app, and switching models is free. `is_cached`
lets the engine load a present CT2 model with `local_files_only` (no network).

`list_models` returns a unified list spanning all three; each entry carries a `kind`, a
human `engine` label, and a `id` ("<kind>:<name>") that `delete_model` routes on.
"""
import os, glob, shutil

# UI/model name -> Hugging Face repo. Covers the names the dropdown offers plus common extras.
REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def cache_dir():
    """The Hugging Face hub cache faster-whisper actually uses (honoring env overrides)."""
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(env):
            return os.environ[env]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    try:
        from huggingface_hub import constants
        return constants.HF_HUB_CACHE
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def _repo_folder(repo):
    return "models--" + repo.replace("/", "--")     # HF hub cache layout


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def is_cached(name):
    """True when this model's weights are already present locally (so no download needed)."""
    repo = REPOS.get(name, name if "/" in str(name) else None)
    if not repo:
        return False
    p = os.path.join(cache_dir(), _repo_folder(repo))
    return os.path.isdir(p) and _dir_size(p) > 1_000_000     # >1 MB = real weights, not a stub


def _mb(nbytes):
    return round(nbytes / (1024 * 1024), 1)


def _ct2_models():
    """Whisper CTranslate2 models in the HF hub cache (NVIDIA / CPU)."""
    root = cache_dir()
    repo2name = {v: k for k, v in REPOS.items()}
    out = []
    for d in glob.glob(os.path.join(root, "models--*")):
        repo = os.path.basename(d)[len("models--"):].replace("--", "/")
        if "whisper" not in repo.lower():                    # ignore unrelated HF models
            continue
        name = repo2name.get(repo, repo)
        out.append({"name": name, "kind": "ct2", "engine": "Whisper (NVIDIA / CPU)",
                    "repo": repo, "size_mb": _mb(_dir_size(d)), "id": "ct2:" + name})
    return out


def _ggml_models():
    """Whisper.cpp GGML models (AMD / Intel GPU via Vulkan or HIP). One file per model name,
    shared by both backends, so there is a single entry regardless of vulkan vs hip."""
    try:
        import whispercpp_setup
        md = whispercpp_setup.models_dir()
    except Exception:
        return []
    out = []
    for p in glob.glob(os.path.join(md, "ggml-*.bin")):
        name = os.path.basename(p)[len("ggml-"):-len(".bin")]
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        out.append({"name": name, "kind": "ggml", "engine": "Whisper (AMD / Intel GPU)",
                    "size_mb": _mb(sz), "id": "ggml:" + name})
    return out


def _parakeet_models():
    """Parakeet (sherpa-onnx) model folders (NVIDIA / CPU)."""
    try:
        import sherpa_setup
        md = sherpa_setup.models_dir()
    except Exception:
        return []
    out = []
    if not os.path.isdir(md):
        return out
    for name in os.listdir(md):
        d = os.path.join(md, name)
        if not os.path.isdir(d):
            continue
        sz = _dir_size(d)
        if sz < 1_000_000:                                   # skip empty/partial folders
            continue
        out.append({"name": name, "kind": "parakeet", "engine": "Parakeet (NVIDIA / CPU)",
                    "size_mb": _mb(sz), "id": "parakeet:" + name})
    return out


def list_models():
    """Every downloaded model across all engines: [{name, kind, engine, size_mb, id, ...}]."""
    out = _ct2_models() + _ggml_models() + _parakeet_models()
    out.sort(key=lambda m: (m["kind"], m["name"]))
    return out


def delete_model(model_id):
    """Remove a downloaded model. Accepts a "<kind>:<name>" id (ct2|ggml|parakeet); a bare
    name is treated as a CTranslate2 Whisper model for backward compatibility."""
    kind, sep, name = model_id.partition(":")
    if not sep:                                              # legacy: bare whisper name/repo
        kind, name = "ct2", model_id

    if kind == "ct2":
        repo = REPOS.get(name, name)
        if "whisper" not in repo.lower():                    # never touch non-whisper caches
            return False
        p = os.path.join(cache_dir(), _repo_folder(repo))
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            return not os.path.isdir(p)
        return False

    if kind == "ggml":
        try:
            import whispercpp_setup
            p = os.path.join(whispercpp_setup.models_dir(), "ggml-%s.bin" % name)
        except Exception:
            return False
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
            return not os.path.exists(p)
        return False

    if kind == "parakeet":
        try:
            import sherpa_setup
            p = os.path.join(sherpa_setup.models_dir(), name)
        except Exception:
            return False
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            return not os.path.isdir(p)
        return False

    return False
