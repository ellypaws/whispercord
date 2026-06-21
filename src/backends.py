"""Transcription backend seam.

One interface, swappable engines:
  * CT2Backend   - faster-whisper / CTranslate2  (device: cuda | cpu)   [implemented]
  * whisper.cpp  - GGML via ctypes               (device: hip | vulkan) [added in a later phase]

A backend takes 16 kHz float32 mono PCM and yields segments exposing ``.text``,
``.no_speech_prob`` and ``.avg_logprob`` (the fields the engine's gating reads). VAD/chunking
and hallucination gating stay in the engine, ABOVE the backend, so behaviour is identical
across engines — only the model call differs.
"""

# whisper.cpp backend (hip/vulkan) is live: the runtime is delegated (downloaded on first GPU use).
# A failed/absent download degrades to CPU gracefully (see load_whispercpp callers), so this is safe
# to leave on even before every artifact is published.
WHISPERCPP_AVAILABLE = True


class CT2Backend:
    """faster-whisper / CTranslate2. A behaviour-identical wrapper over WhisperModel.transcribe
    so the engine's transcribe loop is backend-agnostic."""

    def __init__(self, model, *, beam_size, language, use_vad, no_speech_threshold,
                 transcribe_sounds=True):
        self._model = model
        self._beam = beam_size
        self._lang = language
        self._vad = use_vad
        self._no_speech = no_speech_threshold
        self._transcribe_sounds = bool(transcribe_sounds)

    def transcribe(self, audio, *, transcribe_sounds=None):
        allow_sounds = self._transcribe_sounds if transcribe_sounds is None else bool(transcribe_sounds)
        opts = {
            "beam_size": self._beam,
            "language": self._lang,
            "vad_filter": self._vad,
            "vad_parameters": {"min_silence_duration_ms": 300} if self._vad else None,
            "no_speech_threshold": self._no_speech,
            "condition_on_previous_text": False,       # avoid repeat/hallucination loops
        }
        if allow_sounds:
            opts["suppress_tokens"] = []
        segs, _ = self._model.transcribe(audio, **opts)
        return segs


class WhisperCppBackend:
    """whisper.cpp (GGML via ctypes) for AMD/Intel GPUs (vulkan/hip). Same transcribe(audio)->segs
    contract as CT2Backend; segments expose .text/.no_speech_prob/.avg_logprob. Note: whisper.cpp
    has no built-in Silero VAD pre-filter, so the engine's upstream RMS/speaking gating carries it
    (the in-model VAD that CT2Backend uses is not applied here)."""

    def __init__(self, wcpp, *, beam_size, language, no_speech_threshold, transcribe_sounds=True):
        self._w = wcpp
        self._beam = beam_size
        self._lang = language
        self._no_speech = no_speech_threshold
        self._transcribe_sounds = bool(transcribe_sounds)

    def transcribe(self, audio, *, transcribe_sounds=None):
        allow_sounds = self._transcribe_sounds if transcribe_sounds is None else bool(transcribe_sounds)
        return self._w.transcribe(audio, language=self._lang, beam_size=self._beam,
                                  no_speech_threshold=self._no_speech,
                                  suppress_nst=not allow_sounds)


def load_whispercpp(device, gfx, model_name, *, beam_size, language, no_speech_threshold,
                    transcribe_sounds=True, log=print, on_progress=None):
    """Download (if needed) the whisper.cpp lib variant for `device`(+gfx) and the GGML model,
    then return a ready WhisperCppBackend. Raises if the artifact/model can't be obtained."""
    import whispercpp_setup, whispercpp_ffi
    dll = whispercpp_setup.ensure_lib(device, gfx, log=log, on_progress=on_progress)
    model_path = whispercpp_setup.ensure_model(model_name, log=log, on_progress=on_progress)
    w = whispercpp_ffi.WhisperCpp(dll, model_path, use_gpu=(device in ("vulkan", "hip")))
    return WhisperCppBackend(w, beam_size=beam_size, language=language,
                             no_speech_threshold=no_speech_threshold,
                             transcribe_sounds=transcribe_sounds)
