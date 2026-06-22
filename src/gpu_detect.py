"""GPU / accelerator detection and device routing.

Resolves the configured ``device`` (auto | cuda | hip | vulkan | cpu) to a CONCRETE backend
choice based on what the machine can actually run. CTranslate2 (faster-whisper) serves cuda+cpu;
the whisper.cpp backend (added later) serves hip (AMD ROCm) and vulkan (any GPU).

Detection is Windows-first (the only shipped platform) and dependency-free: NVIDIA via
CTranslate2's driver query, AMD/Intel via the OS video-controller list — no ROCm/Vulkan SDK
needed — so routing works BEFORE any GPU runtime is downloaded (needed to pick the per-arch
HIP artifact). Off Windows it degrades to NVIDIA-or-cpu.
"""
import os, sys, subprocess

# Best-effort GPU-name -> AMD gfx (LLVM target). Used only to pick the per-arch HIP artifact;
# an unrecognised AMD card just routes to vulkan. PROVISIONAL — refine against the actual
# CI artifact set (P3). Detection needs no ROCm installed.
_AMD_GFX = [
    # (name substrings, gfx target)
    (("rx 9070", "ai pro r9700"),                       "gfx1201"),  # RDNA4
    (("rx 9060",),                                       "gfx1200"),  # RDNA4
    (("rx 7900", "w7900", "w7800", "pro w7900", "pro w7800"), "gfx1100"),  # RDNA3
    (("rx 7800", "rx 7700"),                             "gfx1101"),  # RDNA3
    (("rx 7600", "7700s", "7600s"),                      "gfx1102"),  # RDNA3
    (("ryzen ai max", "radeon 8050s", "radeon 8060s"),  "gfx1151"),  # RDNA3.5 (Strix Halo)
    (("890m", "880m"),                                   "gfx1150"),  # RDNA3.5 (Strix Point)
    (("780m", "760m", "740m"),                           "gfx1103"),  # RDNA3 APU (Phoenix)
    (("rx 6950", "rx 6900", "rx 6800", "w6800"),         "gfx1030"),  # RDNA2
    (("rx 6750", "rx 6700"),                             "gfx1031"),  # RDNA2
    (("rx 6650", "rx 6600"),                             "gfx1032"),  # RDNA2
    (("rx 6500", "rx 6400"),                             "gfx1034"),  # RDNA2
]

# gfx targets we plan to ship a whisper.cpp HIP build for (aligned to the P3 artifact matrix).
# RDNA3/3.5/4; older AMD (RDNA2) routes to vulkan, which is the broad fallback.
HIP_GFX_SUPPORTED = {"gfx1100", "gfx1101", "gfx1102", "gfx1150", "gfx1151", "gfx1200", "gfx1201"}

# auto prefers HIP for supported Radeon (RDNA3/3.5/4); the engine's hip->vulkan->cpu fallback
# covers any arch whose HIP artifact isn't published yet or that fails to load.
HIP_ENABLED = True

_NO_WINDOW = 0x08000000


def nvidia_present():
    try:
        from cuda_setup import nvidia_gpu_present
        return nvidia_gpu_present()
    except Exception:
        return False


def _video_controllers():
    """GPU controllers on Windows: [{'name','pnp'}]. Empty off-Windows / on error."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "ForEach-Object { $_.Name + '|' + $_.PNPDeviceID }"],
            capture_output=True, text=True, timeout=12, creationflags=_NO_WINDOW).stdout
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if "|" in line:
                name, pnp = line.split("|", 1)
                rows.append({"name": name.strip(), "pnp": pnp.strip().upper()})
        return rows
    except Exception:
        return []


def _gfx_for(name):
    n = (name or "").lower()
    for subs, gfx in _AMD_GFX:
        if any(s in n for s in subs):
            return gfx
    return None


def amd_gpu():
    """(gfx, name) for the first AMD (VEN_1002) GPU, else (None, None). gfx may be None for a
    recognised-AMD-but-unmapped card (-> route to vulkan)."""
    for c in _video_controllers():
        if "VEN_1002" in c["pnp"]:
            return _gfx_for(c["name"]), c["name"]
    return None, None


def nvidia_gpu_name():
    """Name of the first NVIDIA GPU from the OS controller list, else None."""
    for c in _video_controllers():
        if "VEN_10DE" in c["pnp"]:
            return c["name"]
    return None


def intel_gpu_name():
    """Name of the first Intel GPU from the OS controller list, else None."""
    for c in _video_controllers():
        if "VEN_8086" in c["pnp"]:
            return c["name"]
    return None


def has_vulkan_gpu():
    """Any Vulkan-capable GPU present? Proxy: a real GPU vendor + the Vulkan loader installed
    (vulkan-1.dll ships with the GPU driver)."""
    loader = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "vulkan-1.dll")
    if not os.path.exists(loader):
        return False
    for c in _video_controllers():
        if any(v in c["pnp"] for v in ("VEN_1002", "VEN_10DE", "VEN_8086")):
            return True
    return False


def _wcpp_or_cpu(backend, log):
    """hip/vulkan need the whisper.cpp backend; until it lands, degrade to cpu (with a note)."""
    try:
        import backends
        ok = backends.WHISPERCPP_AVAILABLE
    except Exception:
        ok = False
    if ok:
        return backend
    log("[gpu] '%s' selected but the whisper.cpp backend isn't installed yet - using cpu for now"
        % backend)
    return "cpu"


def resolve(requested, log=print):
    """Map a configured device to a concrete backend: cuda | hip | vulkan | cpu.

    auto -> supported-AMD? hip : Vulkan-GPU? vulkan : NVIDIA? cuda : cpu.
    Vulkan is preferred over CUDA even on NVIDIA: the whisper.cpp Vulkan path benchmarked
    faster than CTranslate2/CUDA here (small and large-v3) at equal accuracy, and it skips the
    ~1.5 GB cuBLAS/cuDNN download. CUDA stays the fallback when Vulkan or the whisper.cpp backend
    isn't usable on an NVIDIA box. Explicit cuda with no NVIDIA GPU falls back to cpu."""
    req = (requested or "auto").strip().lower()

    if req == "cpu":
        return "cpu"
    if req == "cuda":
        if nvidia_present():
            return "cuda"
        log("[gpu] device=cuda but no NVIDIA GPU detected (CUDA is NVIDIA-only) - using cpu")
        return "cpu"
    if req == "vulkan":
        return _wcpp_or_cpu("vulkan", log)
    if req == "hip":
        if HIP_ENABLED:
            return _wcpp_or_cpu("hip", log)
        log("[gpu] hip runtime not published yet - using vulkan")
        return _wcpp_or_cpu("vulkan", log)

    # auto: supported Radeon prefers HIP; otherwise prefer Vulkan (whisper.cpp) for ANY Vulkan
    # GPU, NVIDIA included, since it benchmarked faster than CUDA. CUDA is the NVIDIA fallback.
    gfx, name = amd_gpu()
    if HIP_ENABLED and gfx in HIP_GFX_SUPPORTED:
        log("[gpu] auto: AMD %s (%s) -> hip" % (name, gfx))
        return _wcpp_or_cpu("hip", log)
    if has_vulkan_gpu():
        vk = _wcpp_or_cpu("vulkan", log)
        if vk == "vulkan":
            log("[gpu] auto: Vulkan-capable GPU -> vulkan")
            return "vulkan"
        # whisper.cpp backend unavailable: keep CUDA on NVIDIA rather than dropping to cpu.
        if nvidia_present():
            log("[gpu] auto: vulkan backend unavailable - using cuda")
            return "cuda"
        return vk
    if nvidia_present():
        log("[gpu] auto: no Vulkan loader - using cuda")
        return "cuda"
    log("[gpu] auto: no usable GPU detected - using cpu")
    return "cpu"


def resolve_parakeet(requested, log=print):
    """Map a Parakeet device request to cuda | cpu.

    sherpa-onnx Parakeet supports NVIDIA CUDA and CPU only. hip/vulkan are
    whisper.cpp devices and never apply to this backend.
    """
    req = (requested or "auto").strip().lower()

    if req == "cpu":
        return "cpu"
    if req in ("cuda", "auto"):
        if nvidia_present() or nvidia_gpu_name():
            return "cuda"
        if req == "cuda":
            log("[gpu] parakeet device=cuda but no NVIDIA GPU detected - using cpu")
        return "cpu"

    log("[gpu] parakeet does not support %s (NVIDIA/CPU only) - using cpu" % req)
    return "cpu"


def hardware_summary():
    """Best-effort hardware summary for the setup wizard."""
    nvidia = nvidia_gpu_name()
    gfx, amd = amd_gpu()
    intel = intel_gpu_name()
    if nvidia:
        return {"vendor": "nvidia", "name": nvidia, "gfx": None, "vulkan": has_vulkan_gpu()}
    if amd:
        return {"vendor": "amd", "name": amd, "gfx": gfx, "vulkan": has_vulkan_gpu()}
    if intel:
        return {"vendor": "intel", "name": intel, "gfx": None, "vulkan": has_vulkan_gpu()}
    return {"vendor": "cpu", "name": "CPU", "gfx": None, "vulkan": False}


if __name__ == "__main__":
    print("nvidia:", nvidia_present())
    print("nvidia name:", nvidia_gpu_name())
    print("amd:", amd_gpu())
    print("intel:", intel_gpu_name())
    print("vulkan-capable:", has_vulkan_gpu())
    for d in ("auto", "cuda", "hip", "vulkan", "cpu"):
        print("resolve(%-7s) ->" % d, resolve(d, log=lambda *a: None))
    for d in ("auto", "cuda", "hip", "vulkan", "cpu"):
        print("resolve_parakeet(%-7s) ->" % d, resolve_parakeet(d, log=lambda *a: None))
