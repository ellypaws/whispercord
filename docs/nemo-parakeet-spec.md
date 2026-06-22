# NeMo Parakeet (sherpa-onnx) backend + setup wizard — implementation spec

Status: **designed, not yet built.** Self-contained so it can be handed to another LLM/engineer
without further context. Build it in the existing pywebview UI (vanilla JS, no framework, no build
step) and the existing Python engine. Phase 1 is the deliverable; Phase 2 is documented so it can be
slotted in later without redesign.

## Goal

Add a **third ASR engine** alongside Whisper: NVIDIA **Parakeet-TDT-0.6b-v3** via
[sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/). It is an offline, high-throughput, multilingual
(25 European languages) transducer that tops the HF open-ASR leaderboard for speed and is competitive
on accuracy. It is an **opt-in alternative**, not a replacement — Whisper stays the default and the
only path for languages outside the 25 and for AMD/Intel GPU acceleration.

The seam already exists: `src/backends.py` defines `transcribe(audio) -> segments` and the engine does
all VAD/chunking/gating **above** the backend. Adding a backend is the same move that added
whisper.cpp (see the [[amd-vulkan-decision]] memory / `WhisperCppBackend`).

## Verified facts (do not re-litigate — these were researched, not assumed)

1. **Pre-exported ONNX exists.** Download the release tarball
   `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2`.
   It contains `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`, `test_wavs/`.
   Load with `sherpa_onnx.OfflineRecognizer.from_transducer(encoder, decoder, joiner, tokens, ...)`.
   The `vocab_size`/`context_size` metadata error reported in sherpa-onnx issue #2226 only affects
   **self-exported** models; the pre-exported archive above has the metadata baked in. **Use the
   pre-exported archive — do not export from NeMo ourselves.**
   **int8 is the only first-party v3 distribution (~640 MB) and is the locked default — there is no
   official fp32/fp16 v3 archive.** For a transducer, int8 costs negligible WER (sub-1% absolute) and
   on CPU is *faster* than fp32 would be (int8 GEMM), so it is the bang-for-buck choice, not a
   compromise. Do NOT add an fp32 toggle for v3 (the only fp32 route is self-export, which reintroduces
   the metadata gotcha above). Quality/coverage choices belong on the model axis (`parakeet_model`), not
   the quantization axis — see below.
2. **Languages: 25 European, auto-detect only.** bg, hr, cs, da, nl, en, et, fi, fr, de, el, hu, it,
   lv, lt, mt, pl, pt, ro, sk, sl, es, sv, ru, uk. The transducer **auto-detects per inference and has
   no language-forcing knob** — you cannot pin it to one language the way Whisper accepts `language=`.
   This is the single most important UI consequence (see the matrix below).
3. **GPU = NVIDIA-only in phase 1.** sherpa-onnx GPU is the onnxruntime CUDA provider: install the
   `+cuda` wheel (`pip install sherpa-onnx==<ver>+cuda... -f https://k2-fsa.github.io/sherpa/onnx/cuda.html`
   plus `onnxruntime-gpu`), requires CUDA + cuDNN, pass `provider="cuda"`, and **set `num_threads=1`**
   on GPU. DirectML (any DX12 GPU incl. AMD/Intel) is real but has **no prebuilt wheels** (build from
   source, `SHERPA_ONNX_ENABLE_DIRECTML=ON`) → Phase 2. CPU works everywhere via the plain wheel.
4. **No streaming for v3.** sherpa-onnx issue #2918: Parakeet-TDT-0.6b-v3 is offline-only; pseudo-
   streaming degrades as the buffer grows. The mature streaming path (streaming Zipformer) only covers
   zh-en / bilingual, NOT 25 EU languages → Phase 2, with a coverage tradeoff.
5. **No sound-event tokens, no confidence fields.** Parakeet does not emit `[laughs]`/`♪` events, and
   the transducer result has no `no_speech_prob`/`avg_logprob`. The backend synthesizes neutral values;
   upstream RMS/VAD/speaking gating carries quality control (same situation as `WhisperCppBackend`,
   which also has no in-model VAD).

Sources: [Parakeet-tdt-0.6b-v3 (HF)](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) ·
[sherpa-onnx pre-exported model](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8) ·
[NeMo transducer models doc](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html) ·
[GPU/CUDA install](https://k2-fsa.github.io/sherpa/onnx/python/install.html) ·
[streaming issue #2918](https://github.com/k2-fsa/sherpa-onnx/issues/2918).

## The backend × language × device matrix (the crux — selection MUST obey this)

| Engine (`asr_engine`) | Runtime | Languages | Devices it can use | Lang forcing? | Sound events |
|---|---|---|---|---|---|
| `whisper` (default) | faster-whisper (CT2) + whisper.cpp | **99**, auto + forceable | `cuda` (NVIDIA), `hip`/`vulkan` (AMD/Intel via whisper.cpp), `cpu` | yes | yes |
| `parakeet` | sherpa-onnx transducer | **25 European**, auto-detect only | `cuda` (NVIDIA), `cpu` *(directml = Phase 2)* | **no** | no |

Hard rules the UI and the router must both enforce:

- Selecting **`parakeet`** disables/replaces the language picker with a static **"Auto (25 European
  languages)"** and restricts the device picker to **auto / cuda / cpu**. `hip` and `vulkan` are not
  applicable — grey them out with a tooltip: *"AMD/Intel GPU acceleration isn't available for Parakeet
  — use Whisper for hip/vulkan, or Parakeet on CPU."*
- Selecting **`whisper`** restores the full language list and all five devices (unchanged from today).
- `device: auto` under `parakeet` resolves to **`cuda` if NVIDIA present else `cpu`** (never hip/vulkan).

## Where the code lives

- `src/backends.py` — backend seam. Add `SherpaOnnxBackend` + a `load_sherpa_onnx(...)` loader next to
  `load_whispercpp`.
- `src/live_transcribe.py` — engine `main()` (~lines 1088–1172) does device routing and constructs the
  backend. Add the engine-level branch BEFORE the existing device routing.
- `src/gpu_detect.py` — `resolve()` maps device→concrete. Add a `resolve_parakeet(requested)` (or a
  param) that only ever returns `cuda` | `cpu`.
- `src/config.py` — `DEFAULTS`. Add `asr_engine`, `parakeet_model`, `setup_completed`.
- New `src/sherpa_setup.py` — delegated download of the model archive AND the sherpa-onnx wheel/native
  libs, mirroring `whispercpp_setup.py` / `pkg_setup` / `cuda_setup`. **NEVER bundle these in the exe**
  — the delivery model is a small exe that downloads what each machine needs (see [[amd-vulkan-decision]]).
- `src/ui/index.html` + `src/ui/app.js` — settings UI (Engine section ~lines 441–505) and the new
  first-run wizard. `src/app.py` — pywebview `Api` bridge (add wizard/detection endpoints).

## Config schema changes

Add to `config.py` `DEFAULTS`:

```python
"asr_engine": "whisper",                 # whisper | parakeet  (which ASR engine to run)
"parakeet_model": "parakeet-tdt-0.6b-v3-int8",  # locked-int8 default; see "Model options" for the menu
"setup_completed": False,                # first-run setup wizard finished (separate from keyword_onboarded)
```

Keep `whisper_model`, `language`, `beam_size`, `device`, `compute_type` — they apply when
`asr_engine == "whisper"`. Under `parakeet`, `language`/`beam_size`/`compute_type` are ignored and
`device` is constrained to auto/cuda/cpu.

### Model options (the `parakeet_model` menu)

Quantization is NOT a user choice (int8 only — see verified fact 1). The user-facing choice is *which
model*, exposed as `parakeet_model` values. `sherpa_setup.ensure_model` maps each to its release archive.

| `parakeet_model` | Coverage | When to pick |
|---|---|---|
| `parakeet-tdt-0.6b-v3-int8` **(default)** | 25 European languages, auto-detect | best all-round bang-for-buck; the default for everyone |
| `parakeet-tdt-0.6b-v2-int8` | English only | marginally better English WER for an English-only server |
| `canary-1b-v2` *(future / Phase-2-ish)* | 25 EU + speech translation | more accuracy + translation, slower; out of scope here |

Phase 1 only needs to ship the **v3 default**. Surface the menu as a small "Model" `<select>` shown
only when `asr_engine == parakeet` (or leave it config-only for v1 and add the select later) — but keep
the `parakeet_model` key from day one so the seam exists.

---

# Phase 1 — offline Parakeet backend, backend-aware selection, setup wizard

### 1. `SherpaOnnxBackend` (in `backends.py`)

Mirror the `WhisperCppBackend` contract exactly. `transcribe(audio)` receives **16 kHz float32 mono**
(the engine already converts int16→float32 before calling the backend) and must return an iterable of
segment-like objects exposing `.text`, `.no_speech_prob`, `.avg_logprob`.

```python
class _Seg:
    __slots__ = ("text", "no_speech_prob", "avg_logprob")
    def __init__(self, text):
        self.text = text
        self.no_speech_prob = 0.0    # synthesized: Parakeet has no such signal; upstream gating carries quality
        self.avg_logprob = 0.0

class SherpaOnnxBackend:
    """NVIDIA Parakeet-TDT-0.6b-v3 via sherpa-onnx (offline transducer). 25 EU languages, auto-detect.
    No in-model VAD and no sound-event tokens — upstream RMS/speaking gating carries quality."""
    def __init__(self, recognizer):
        self._rec = recognizer

    def transcribe(self, audio, *, transcribe_sounds=None):
        # transcribe_sounds is a no-op (Parakeet emits no [laughs]/♪ tokens)
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, audio)        # audio is float32 in [-1, 1]
        self._rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
        return [_Seg(text)] if text else []


def load_sherpa_onnx(model_name, device, *, num_threads=4, log=print, on_progress=None):
    """Download (if needed) the sherpa-onnx wheel/libs + the model archive, then return a ready
    SherpaOnnxBackend. device is 'cuda' or 'cpu' (resolved upstream). Raises if unobtainable."""
    import sherpa_setup
    sherpa = sherpa_setup.ensure_runtime(device, log=log, on_progress=on_progress)  # imports sherpa_onnx
    d = sherpa_setup.ensure_model(model_name, log=log, on_progress=on_progress)     # extracted dir
    provider = "cuda" if device == "cuda" else "cpu"
    rec = sherpa.OfflineRecognizer.from_transducer(
        encoder=f"{d}/encoder.int8.onnx", decoder=f"{d}/decoder.int8.onnx",
        joiner=f"{d}/joiner.int8.onnx", tokens=f"{d}/tokens.txt",
        num_threads=(1 if provider == "cuda" else num_threads),   # GPU: force 1 (verified guidance)
        sample_rate=16000, feature_dim=80,
        decoding_method="greedy_search", provider=provider)
    return SherpaOnnxBackend(rec)
```

Set a `SHERPA_ONNX_AVAILABLE = True` flag alongside `WHISPERCPP_AVAILABLE` so routing can degrade
gracefully if the download/import fails (fall back to CPU sherpa, then to Whisper, with a logged note).

### 2. `sherpa_setup.py` (delegated download — NEVER bundle)

Same pattern as `whispercpp_setup.py`:

- `ensure_model(name)` — download `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2` into the
  app-owned model cache (`model_store.cache_dir()`), extract once, return the dir. Re-mirror the asset
  under the project's own release tag (as done for whisper.cpp via `OUR_BASE`) so downloads don't
  depend on upstream availability. The int8 archive is the small/practical choice.
- `ensure_runtime(device)` — make `import sherpa_onnx` work. CPU: the plain wheel (delegate like
  ct2/onnxruntime via the existing `pkg_setup` mechanism). CUDA: the `+cuda` wheel + onnxruntime-gpu +
  the CUDA/cuDNN DLLs (reuse `add_cuda_dlls()` / `cuda_setup`). Report download progress through
  `on_progress` so the UI shows the same model-download bar Whisper uses.

### 3. Engine routing (`live_transcribe.py`)

Add an engine-level branch BEFORE the existing device routing:

```python
ENGINE = CFG.get("asr_engine", "whisper")
if ENGINE == "parakeet":
    dev = gpu_detect.resolve_parakeet(DEVICE, print)     # -> 'cuda' | 'cpu'
    backend = backends.load_sherpa_onnx(
        CFG.get("parakeet_model", "parakeet-tdt-0.6b-v3-int8"), dev,
        log=print, on_progress=lambda pct, label: emit_progress("model", label, pct))
else:
    ... existing whisper (cuda/hip/vulkan/cpu) routing unchanged ...
```

The interim/final loop, silence/utterance logic, and gating are unchanged. Note in code that
`transcribe_sounds` and `language` config are inert for Parakeet.

### 4. `gpu_detect.resolve_parakeet(requested)`

```python
def resolve_parakeet(requested, log=print):
    req = (requested or "auto").strip().lower()
    if req == "cpu":
        return "cpu"
    if req in ("cuda", "auto"):
        if nvidia_present():
            return "cuda"
        if req == "cuda":
            log("[gpu] parakeet device=cuda but no NVIDIA GPU - using cpu")
        return "cpu"
    # hip/vulkan are not applicable to Parakeet
    log("[gpu] parakeet does not support %s (NVIDIA/CPU only) - using cpu" % req)
    return "cpu"
```

### 5. Backend-aware settings UI (`index.html` + `app.js`)

- Add an **Engine** `<select id="asr_engine">` with `whisper` / `parakeet` above the existing model row.
- On change (and on load), call a `applyEngineConstraints(engine)` that:
  - `parakeet`: hide the `whisper_model` row, replace `#adv_lang` with a disabled "Auto (25 European
    languages)" control, remove `hip`/`vulkan` options from `#adv_device` (or disable with the tooltip
    above), hide `beam_size`/`compute_type`. Show a one-line note: *"Parakeet — fast multilingual
    (25 EU langs), NVIDIA or CPU. Model downloads on first Start."*
  - `whisper`: restore everything (current behavior).
- Persist `asr_engine` in the save path next to the other engine keys.

### 6. Sleeker first-time setup wizard

Today there is only a keyword popup gated by `keyword_onboarded`. Add a proper multi-step wizard gated
by a new `setup_completed` flag, and a **manual re-entry** ("Setup wizard" button in settings; optional
`--setup` CLI flag). Keep it in the same pywebview modal style as `openKeywordSetup`.

Backend endpoints to add to `app.py` `Api` (reuse existing logic where noted):

- `detect_hardware()` → `{vendor: "nvidia"|"amd"|"intel"|"cpu", name, recommended_engine, recommended_device}`.
  Build from `gpu_detect.nvidia_present()` / `amd_gpu()` / `has_vulkan_gpu()`. Recommendation logic:
  NVIDIA → offer either (Whisper-cuda for 99 langs, or Parakeet-cuda for speed); AMD/Intel → Whisper
  (hip/vulkan), since Parakeet has no GPU there; CPU-only → Parakeet-cpu is a strong default (fast).
- `list_clients()` / `ensure_client(folder, restart)` already exist — reuse for the launch step.
- `get_config` / `save_config` already exist — wizard writes through these.

Wizard steps (each a panel in one modal, Back/Next):

1. **Engine & device.** Show *"Detected: NVIDIA GeForce RTX 4080"* etc. Present the recommended choice
   pre-selected, with the other valid options. Enforce the matrix live (e.g. picking Parakeet hides the
   language step's free choice; picking Parakeet on an AMD box shows "GPU not available for Parakeet —
   will run on CPU; pick Whisper for GPU"). Note the one-time model/runtime download.
2. **Language** (only shown for Whisper; skipped/auto for Parakeet).
3. **Keywords / alert on your name.** Reuse `keywordSuggestions(selfNames)` + the existing keyword
   modal body. Sets `keyword_onboarded`.
4. **Launch Discord with debug port.** Reuse the client list + the "Launch / Restart w/ port" flow and
   the existing port-CTA glow. Explain in one line why the port is needed (CDP for names/overlay).
5. **Done.** Write `setup_completed: true`, save, close. (Step 1's engine/device and step 2's language
   are written to config on Finish.)

First-run trigger: in `app.js`, where the keyword popup currently fires, instead open the full wizard
when `!CFG.setup_completed`. Keep the standalone keyword popup as a fallback only if `setup_completed`
is already true but `keyword_onboarded` is false (upgrade path for existing users).

### Phase 1 acceptance criteria

- `asr_engine: "parakeet"` transcribes Discord voice end-to-end on CPU, and on `cuda` when an NVIDIA GPU
  is present, using the delegated-downloaded model (nothing heavy bundled in the exe).
- Selecting Parakeet in settings disables language forcing (shows "Auto (25 EU)"), hides
  beam/compute, and removes hip/vulkan from the device options; selecting Whisper restores all of it.
- `device: auto` + Parakeet → cuda on NVIDIA, cpu otherwise; explicit hip/vulkan + Parakeet → cpu with a
  logged note. A failed sherpa download/import degrades gracefully (CPU sherpa → Whisper) without crash.
- First run opens the wizard (detect HW → engine/device → language → keywords → launch w/ port); on
  finish `setup_completed` is set and it does not reappear. A "Setup wizard" button re-opens it.
- Existing Whisper behavior (all 5 devices, 99 langs, sound events, gating) is byte-for-byte unchanged.

---

# Phase 2 — streaming + DirectML (deferred; documented to slot in later)

Two **independent** tracks. Neither is a free win — keep both opt-in/experimental. Do not build until
real demand justifies it.

### 2A. True streaming (latency ↔ coverage tradeoff)

- Parakeet-TDT-0.6b-v3 **cannot stream** in sherpa-onnx (issue #2918). "FastConformer" is just the
  encoder Parakeet already uses; streaming needs a *cache-aware streaming* export, not published for v3.
- The mature streaming path is **streaming Zipformer**, but the available models are **bilingual
  zh-en / trilingual** — adopting it trades the 25-EU-language coverage for a much narrower set, plus a
  small accuracy hit from limited right-context. With utterances capped at `max_utt_s=12` and offline
  Parakeet already fast, the latency payoff is modest.
- If built: add a `StreamingSherpaBackend` using `sherpa_onnx.OnlineRecognizer` with per-speaker
  `OnlineStream` state + sherpa's endpoint detection (could replace the `interim_every_s` re-transcribe
  loop and the silence heuristic). Add a config flag (e.g. `parakeet_streaming: false`) and surface the
  language-coverage downgrade clearly in the UI. Evaluate streaming Zipformer vs any future cache-aware
  streaming FastConformer export at that time.

### 2B. DirectML GPU for AMD/Intel

- sherpa-onnx DirectML provider runs on any DX12 GPU (AMD/Intel/NVIDIA on Windows), sidestepping
  ROCm/Vulkan — it would give non-NVIDIA users GPU on the Parakeet backend (Phase 1 leaves them on CPU).
- Blocker: **no prebuilt DirectML Python wheels** — requires building sherpa-onnx from source
  (`SHERPA_ONNX_ENABLE_DIRECTML=ON`, `BUILD_SHARED_LIBS=ON`) and shipping those native libs through the
  same delegated-download channel as the HIP/Vulkan whisper.cpp artifacts. `provider="directml"`,
  `num_threads=1`.
- If built: extend `resolve_parakeet` to return `directml` for a DX12 AMD/Intel GPU, and
  `sherpa_setup.ensure_runtime` to fetch the DirectML build per the existing CI/release pattern.

## Out of scope (do not build in either phase)

- Self-exporting NeMo→ONNX (use the pre-exported archive).
- Canary-1b-v2 / speech translation (could be a future `parakeet_model`/`asr_engine` variant reusing
  the same sherpa plumbing, but not part of this spec).
- Any change to the Whisper engines, the gating pipeline, or the relay/overlay protocol.
