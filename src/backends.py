"""Transcription backend seam.

One interface, swappable engines:
  * CT2Backend   - faster-whisper / CTranslate2  (device: cuda | cpu)   [implemented]
  * whisper.cpp  - GGML via ctypes               (device: hip | vulkan) [added in a later phase]

A backend takes 16 kHz float32 mono PCM and yields segments exposing ``.text``,
``.no_speech_prob`` and ``.avg_logprob`` (the fields the engine's gating reads). VAD/chunking
and hallucination gating stay in the engine, ABOVE the backend, so behaviour is identical
across engines — only the model call differs.
"""
import os

# whisper.cpp backend (hip/vulkan) is live: the runtime is delegated (downloaded on first GPU use).
# A failed/absent download degrades to CPU gracefully (see load_whispercpp callers), so this is safe
# to leave on even before every artifact is published.
WHISPERCPP_AVAILABLE = True
SHERPA_ONNX_AVAILABLE = True


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


class _SherpaSeg:
    __slots__ = ("text", "no_speech_prob", "avg_logprob")

    def __init__(self, text):
        self.text = text
        self.no_speech_prob = 0.0
        self.avg_logprob = 0.0


class SherpaOnnxBackend:
    """NVIDIA Parakeet-TDT-0.6b-v3 via sherpa-onnx. It is offline-only, auto-detects
    25 European languages, and does not emit sound-event tokens or confidence fields."""

    def __init__(self, recognizer):
        self._rec = recognizer

    def transcribe(self, audio, *, transcribe_sounds=None):
        # transcribe_sounds is inert for Parakeet because the transducer emits text only.
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, audio)
        self._rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
        return [_SherpaSeg(text)] if text else []


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


def load_sherpa_onnx(model_name, device, *, num_threads=4, log=print, on_progress=None):
    """Download sherpa-onnx + Parakeet if needed, then return a ready SherpaOnnxBackend.
    device must already be resolved to cuda or cpu."""
    import sherpa_setup
    sherpa = sherpa_setup.ensure_runtime(device, log=log, on_progress=on_progress)
    d = sherpa_setup.ensure_model(model_name, log=log, on_progress=on_progress)
    provider = "cuda" if device == "cuda" else "cpu"
    if on_progress:
        on_progress(None, "Loading Parakeet model")
    rec = sherpa.OfflineRecognizer.from_transducer(
        encoder=os.path.join(d, "encoder.int8.onnx"),
        decoder=os.path.join(d, "decoder.int8.onnx"),
        joiner=os.path.join(d, "joiner.int8.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=1 if provider == "cuda" else num_threads,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        provider=provider,
        # Parakeet is a NeMo TDT transducer. Without this the loader defaults to the
        # zipformer/icefall path and dies with "'vocab_size' does not exist".
        model_type="nemo_transducer",
    )
    return SherpaOnnxBackend(rec)
