"""ctypes binding for whisper.cpp (pinned to the v1.9.1 C ABI).

Structs/signatures are transcribed verbatim from whisper.cpp v1.9.1 ``include/whisper.h`` —
``whisper_full_params`` is passed BY VALUE, so the layout must be byte-exact (validated against
a real CPU whisper.dll in CI/tests). The GPU variants (Vulkan/HIP) expose the identical API, so
only the loaded DLL differs.

Exposes a small ``WhisperCpp`` class: load a GGML model, feed 16 kHz float32 PCM, get segments
with ``.text`` / ``.no_speech_prob`` / ``.avg_logprob`` (the fields the engine's gating reads).
"""
import os, math, ctypes as C

WHISPER_SAMPLING_GREEDY = 0
WHISPER_SAMPLING_BEAM_SEARCH = 1


class _Greedy(C.Structure):
    _fields_ = [("best_of", C.c_int)]


class _BeamSearch(C.Structure):
    _fields_ = [("beam_size", C.c_int), ("patience", C.c_float)]


class WhisperAheads(C.Structure):
    _fields_ = [("n_heads", C.c_size_t), ("heads", C.c_void_p)]   # const whisper_ahead*


class WhisperContextParams(C.Structure):
    _fields_ = [
        ("use_gpu", C.c_bool),
        ("flash_attn", C.c_bool),
        ("gpu_device", C.c_int),
        ("dtw_token_timestamps", C.c_bool),
        ("dtw_aheads_preset", C.c_int),       # enum whisper_alignment_heads_preset
        ("dtw_n_top", C.c_int),
        ("dtw_aheads", WhisperAheads),
        ("dtw_mem_size", C.c_size_t),
    ]


class WhisperVadParams(C.Structure):
    _fields_ = [
        ("threshold", C.c_float),
        ("min_speech_duration_ms", C.c_int),
        ("min_silence_duration_ms", C.c_int),
        ("max_speech_duration_s", C.c_float),
        ("speech_pad_ms", C.c_int),
        ("samples_overlap", C.c_float),
    ]


class WhisperFullParams(C.Structure):
    _fields_ = [
        ("strategy", C.c_int),
        ("n_threads", C.c_int),
        ("n_max_text_ctx", C.c_int),
        ("offset_ms", C.c_int),
        ("duration_ms", C.c_int),
        ("translate", C.c_bool),
        ("no_context", C.c_bool),
        ("no_timestamps", C.c_bool),
        ("single_segment", C.c_bool),
        ("print_special", C.c_bool),
        ("print_progress", C.c_bool),
        ("print_realtime", C.c_bool),
        ("print_timestamps", C.c_bool),
        ("token_timestamps", C.c_bool),
        ("thold_pt", C.c_float),
        ("thold_ptsum", C.c_float),
        ("max_len", C.c_int),
        ("split_on_word", C.c_bool),
        ("max_tokens", C.c_int),
        ("debug_mode", C.c_bool),
        ("audio_ctx", C.c_int),
        ("tdrz_enable", C.c_bool),
        ("suppress_regex", C.c_char_p),
        ("initial_prompt", C.c_char_p),
        ("carry_initial_prompt", C.c_bool),
        ("prompt_tokens", C.POINTER(C.c_int)),     # const whisper_token* (int32)
        ("prompt_n_tokens", C.c_int),
        ("language", C.c_char_p),
        ("detect_language", C.c_bool),
        ("suppress_blank", C.c_bool),
        ("suppress_nst", C.c_bool),
        ("temperature", C.c_float),
        ("max_initial_ts", C.c_float),
        ("length_penalty", C.c_float),
        ("temperature_inc", C.c_float),
        ("entropy_thold", C.c_float),
        ("logprob_thold", C.c_float),
        ("no_speech_thold", C.c_float),
        ("greedy", _Greedy),
        ("beam_search", _BeamSearch),
        ("new_segment_callback", C.c_void_p),
        ("new_segment_callback_user_data", C.c_void_p),
        ("progress_callback", C.c_void_p),
        ("progress_callback_user_data", C.c_void_p),
        ("encoder_begin_callback", C.c_void_p),
        ("encoder_begin_callback_user_data", C.c_void_p),
        ("abort_callback", C.c_void_p),
        ("abort_callback_user_data", C.c_void_p),
        ("logits_filter_callback", C.c_void_p),
        ("logits_filter_callback_user_data", C.c_void_p),
        ("grammar_rules", C.c_void_p),
        ("n_grammar_rules", C.c_size_t),
        ("i_start_rule", C.c_size_t),
        ("grammar_penalty", C.c_float),
        ("vad", C.c_bool),
        ("vad_model_path", C.c_char_p),
        ("vad_params", WhisperVadParams),
    ]


class _Seg:
    __slots__ = ("text", "no_speech_prob", "avg_logprob")

    def __init__(self, text, no_speech_prob, avg_logprob):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def _bind(lib):
    p = C.c_void_p        # opaque whisper_context*
    lib.whisper_context_default_params.restype = WhisperContextParams
    lib.whisper_context_default_params.argtypes = []
    lib.whisper_init_from_file_with_params.restype = p
    lib.whisper_init_from_file_with_params.argtypes = [C.c_char_p, WhisperContextParams]
    lib.whisper_full_default_params.restype = WhisperFullParams
    lib.whisper_full_default_params.argtypes = [C.c_int]
    lib.whisper_full.restype = C.c_int
    lib.whisper_full.argtypes = [p, WhisperFullParams, C.POINTER(C.c_float), C.c_int]
    lib.whisper_full_n_segments.restype = C.c_int
    lib.whisper_full_n_segments.argtypes = [p]
    lib.whisper_full_get_segment_text.restype = C.c_char_p
    lib.whisper_full_get_segment_text.argtypes = [p, C.c_int]
    lib.whisper_full_get_segment_no_speech_prob.restype = C.c_float
    lib.whisper_full_get_segment_no_speech_prob.argtypes = [p, C.c_int]
    lib.whisper_full_n_tokens.restype = C.c_int
    lib.whisper_full_n_tokens.argtypes = [p, C.c_int]
    lib.whisper_full_get_token_p.restype = C.c_float
    lib.whisper_full_get_token_p.argtypes = [p, C.c_int, C.c_int]
    lib.whisper_free.restype = None
    lib.whisper_free.argtypes = [p]
    # Language auto-detection (optional; guarded so a DLL without these symbols still loads).
    try:
        lib.whisper_pcm_to_mel.restype = C.c_int
        lib.whisper_pcm_to_mel.argtypes = [p, C.POINTER(C.c_float), C.c_int, C.c_int]
        lib.whisper_lang_auto_detect.restype = C.c_int
        lib.whisper_lang_auto_detect.argtypes = [p, C.c_int, C.c_int, C.POINTER(C.c_float)]
        lib.whisper_lang_str.restype = C.c_char_p
        lib.whisper_lang_str.argtypes = [C.c_int]
        lib.whisper_lang_max_id.restype = C.c_int
        lib.whisper_lang_max_id.argtypes = []
        lib._has_lang_detect = True
    except AttributeError:
        lib._has_lang_detect = False
    return lib


class WhisperCpp:
    """Thin handle over a loaded whisper.cpp DLL + GGML model."""

    def __init__(self, lib_path, model_path, n_threads=0, use_gpu=True, flash_attn=False):
        lib_dir = os.path.dirname(os.path.abspath(lib_path))
        try:
            os.add_dll_directory(lib_dir)
        except Exception:
            pass
        # Register ggml's backend DLLs (ggml-cpu-*.dll / ggml-vulkan.dll / ggml-hip.dll) from our
        # lib folder. ggml auto-discovery scans the exe dir / cwd, which is NOT where we extracted
        # them, so point it at lib_dir explicitly — without this, backends=0 and init asserts.
        ggml_path = os.path.join(lib_dir, "ggml.dll")
        if os.path.exists(ggml_path):
            try:
                self._ggml = C.CDLL(ggml_path)
                self._ggml.ggml_backend_load_all_from_path.argtypes = [C.c_char_p]
                self._ggml.ggml_backend_load_all_from_path.restype = None
                self._ggml.ggml_backend_load_all_from_path(lib_dir.encode("utf-8"))
            except Exception:
                pass
        self._lib = _bind(C.CDLL(lib_path))
        cp = self._lib.whisper_context_default_params()
        cp.use_gpu = bool(use_gpu)            # cpu variant -> False; vulkan/hip -> True
        cp.flash_attn = bool(flash_attn)
        self._ctx = self._lib.whisper_init_from_file_with_params(str(model_path).encode("utf-8"), cp)
        if not self._ctx:
            raise RuntimeError("whisper_init_from_file_with_params failed: %s" % model_path)
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) // 2)
        self._lang_buf = None        # keep the language bytes alive across the call
        self._auto_lang = None       # cached auto-detected code when no language is configured

    def _detect_language(self, a):
        """Resolve a CONCRETE language code for whisper_full when none is configured. whisper.cpp
        needs a non-NULL language: passing NULL transcribes on some GPUs but returns EMPTY on others
        (observed on AMD RDNA4 Vulkan). Detect once via whisper_lang_auto_detect, cache the first
        confident result (avoids re-encoding and language flicker on short interim chunks), and fall
        back to 'en' rather than ever handing whisper_full a NULL language."""
        if self._auto_lang:
            return self._auto_lang
        if not getattr(self._lib, "_has_lang_detect", False):
            return "en"
        try:
            import numpy as np
            a = np.ascontiguousarray(a, dtype=np.float32)
            if self._lib.whisper_pcm_to_mel(
                    self._ctx, a.ctypes.data_as(C.POINTER(C.c_float)), a.shape[0], self._n_threads) == 0:
                maxid = self._lib.whisper_lang_max_id() + 1
                probs = (C.c_float * maxid)()
                lid = self._lib.whisper_lang_auto_detect(self._ctx, 0, self._n_threads, probs)
                if 0 <= lid < maxid and probs[lid] >= 0.5:
                    code = self._lib.whisper_lang_str(lid)
                    if code:
                        self._auto_lang = code.decode("utf-8", "replace")   # cache confident detections
                        return self._auto_lang
        except Exception:
            pass
        return self._auto_lang or "en"   # don't cache a low-confidence guess; retry next chunk

    def transcribe(self, audio_f32, language=None, beam_size=1, no_speech_threshold=0.6):
        import numpy as np
        a = np.ascontiguousarray(audio_f32, dtype=np.float32)
        greedy = (beam_size or 1) <= 1
        p = self._lib.whisper_full_default_params(
            WHISPER_SAMPLING_GREEDY if greedy else WHISPER_SAMPLING_BEAM_SEARCH)
        p.n_threads = self._n_threads
        p.print_progress = p.print_realtime = p.print_timestamps = p.print_special = False
        p.no_timestamps = True
        p.translate = False
        p.single_segment = False
        p.no_speech_thold = float(no_speech_threshold)
        if greedy:
            p.greedy.best_of = 1
        else:
            p.beam_search.beam_size = int(beam_size)
        # No language given ("" or None) = auto-detect (parity with faster-whisper). whisper.cpp needs
        # a CONCRETE language: a NULL pointer returns EMPTY segments on some GPUs (AMD RDNA4 Vulkan),
        # and detect_language=True over NULL wedges whisper_full. So resolve a real code ourselves.
        lang = language or self._detect_language(a)
        self._lang_buf = lang.encode("utf-8") if lang else None
        p.language = self._lang_buf
        p.detect_language = False
        rc = self._lib.whisper_full(self._ctx, p, a.ctypes.data_as(C.POINTER(C.c_float)), a.shape[0])
        if rc != 0:
            raise RuntimeError("whisper_full failed rc=%d" % rc)
        out = []
        for i in range(self._lib.whisper_full_n_segments(self._ctx)):
            raw = self._lib.whisper_full_get_segment_text(self._ctx, i)
            text = raw.decode("utf-8", "replace") if raw else ""
            nsp = float(self._lib.whisper_full_get_segment_no_speech_prob(self._ctx, i))
            nt = self._lib.whisper_full_n_tokens(self._ctx, i)
            lp, cnt = 0.0, 0
            for t in range(nt):
                pr = float(self._lib.whisper_full_get_token_p(self._ctx, i, t))
                lp += math.log(max(pr, 1e-8)); cnt += 1
            out.append(_Seg(text, nsp, (lp / cnt) if cnt else 0.0))   # avg_logprob ~ mean log token_p
        return out

    def close(self):
        if getattr(self, "_ctx", None):
            self._lib.whisper_free(self._ctx)
            self._ctx = None
