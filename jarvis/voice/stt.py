"""Speech-to-text: local faster-whisper (CPU int8) with Google Web Speech fallback."""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you", ".", "bye.",
                   "subtitles by the amara.org community", "thank you very much."}
_WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hi|okay|ok|yo)[,\s]+)?(?:jarvis|jervis|travis|jarvi)[\s,.!?:;-]*", re.I)


def strip_wake_word(text: str) -> str:
    return _WAKE_PREFIX.sub("", text).strip()


def clean_transcript(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if t.lower() in _HALLUCINATIONS:
        return ""
    return t


class WhisperSTT:
    name = "whisper"

    def __init__(self, models: Path, model_size: str = "base.en") -> None:
        from jarvis.voice._tokenizers_shim import install, install_av_stub

        install_av_stub()
        install()
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4,
                                  download_root=str(models / "whisper"))
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        with self._lock:
            t = time.time()
            segments, info = self.model.transcribe(
                audio.astype(np.float32), language="en", beam_size=1, best_of=1, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
                initial_prompt="JARVIS, open, close, VS Code, PowerShell, Python, GitHub, Chrome, Spotify.",
            )
            parts = []
            logprobs = []
            for s in segments:
                if s.no_speech_prob > 0.75 and s.avg_logprob < -0.8:
                    continue
                parts.append(s.text)
                logprobs.append(s.avg_logprob)
            text = clean_transcript(" ".join(parts))
            conf = float(np.exp(np.mean(logprobs))) if logprobs else 0.0
            log.debug("whisper %.2fs audio -> %.2fs: %r", audio.shape[0] / 16000, time.time() - t, text)
            return text, conf


class GoogleSTT:
    name = "google"

    def transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        import speech_recognition as sr

        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        buf.seek(0)
        r = sr.Recognizer()
        with sr.AudioFile(buf) as src:
            data = r.record(src)
        try:
            return clean_transcript(r.recognize_google(data, language="en-US")), 0.8
        except sr.UnknownValueError:
            return "", 0.0


class STT:
    """Primary engine with automatic fallback on failure."""

    def __init__(self, models: Path, backend: str = "whisper", model_size: str = "base.en") -> None:
        self.engines: list[object] = []
        self.errors: list[str] = []
        if backend == "whisper":
            try:
                self.engines.append(WhisperSTT(models, model_size))
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"whisper: {exc}")
                log.warning("local Whisper unavailable: %s", exc)
        self.engines.append(GoogleSTT())

    @property
    def primary(self) -> str:
        return getattr(self.engines[0], "name", "?")

    def transcribe(self, audio: np.ndarray) -> tuple[str, float, str]:
        last: Exception | None = None
        for eng in self.engines:
            try:
                text, conf = eng.transcribe(audio)  # type: ignore[attr-defined]
                return text, conf, eng.name  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("STT engine %s failed: %s", getattr(eng, "name", "?"), exc)
        raise RuntimeError(f"all speech recognition engines failed: {last}")
