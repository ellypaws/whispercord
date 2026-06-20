"""Whisper model cache management.

faster-whisper downloads CTranslate2 models from Hugging Face into the standard HF hub
cache. We operate on that SAME cache (rather than a private folder) so nothing is ever
re-downloaded just because of this app, and switching between already-downloaded models is
free. `is_cached` lets the engine load a present model with `local_files_only` (no network).
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


def list_models():
    """Every Whisper model present in the cache: [{name, repo, size_mb, known}]."""
    root = cache_dir()
    repo2name = {v: k for k, v in REPOS.items()}
    out = []
    for d in glob.glob(os.path.join(root, "models--*")):
        repo = os.path.basename(d)[len("models--"):].replace("--", "/")
        if "whisper" not in repo.lower():                    # ignore unrelated HF models
            continue
        out.append({
            "name": repo2name.get(repo, repo),
            "repo": repo,
            "size_mb": round(_dir_size(d) / (1024 * 1024), 1),
            "known": repo in repo2name,
        })
    out.sort(key=lambda m: m["name"])
    return out


def delete_model(name):
    """Remove a cached Whisper model by friendly name or repo id. Returns True if deleted."""
    repo = REPOS.get(name, name)
    if "whisper" not in repo.lower():                        # never touch non-whisper caches
        return False
    p = os.path.join(cache_dir(), _repo_folder(repo))
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
        return not os.path.isdir(p)
    return False
