"""Transcription backend seam.

One interface, swappable engines:
  * CT2Backend   - faster-whisper / CTranslate2  (device: cuda | cpu)   [implemented]
  * whisper.cpp  - GGML via ctypes               (device: hip | vulkan) [added in a later phase]

A backend takes 16 kHz float32 mono PCM and yields segments exposing ``.text``,
``.no_speech_prob`` and ``.avg_logprob`` (the fields the engine's gating reads). VAD/chunking
and hallucination gating stay in the engine, ABOVE the backend, so behaviour is identical
across engines — only the model call differs.
"""

# Flipped on when the whisper.cpp backend (hip/vulkan) lands. Until then gpu_detect.resolve()
# degrades hip/vulkan to cpu so the options are selectable but never break.
WHISPERCPP_AVAILABLE = False


class CT2Backend:
    """faster-whisper / CTranslate2. A behaviour-identical wrapper over WhisperModel.transcribe
    so the engine's transcribe loop is backend-agnostic."""

    def __init__(self, model, *, beam_size, language, use_vad, no_speech_threshold):
        self._model = model
        self._beam = beam_size
        self._lang = language
        self._vad = use_vad
        self._no_speech = no_speech_threshold

    def transcribe(self, audio):
        segs, _ = self._model.transcribe(
            audio, beam_size=self._beam, language=self._lang,
            vad_filter=self._vad,
            vad_parameters={"min_silence_duration_ms": 300} if self._vad else None,
            no_speech_threshold=self._no_speech,
            condition_on_previous_text=False,          # avoid repeat/hallucination loops
        )
        return segs
