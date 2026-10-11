"""Speech-to-text and wake-phrase matching.

Measured on this user's YS-511 Bluetooth headset (hands-free mode, telephone-band
audio, nothing above 3.4 kHz), Google's recogniser transcribed requests correctly
("can you close vs code") where local Whisper base.en produced unrelated text and
hallucinations ("We'll see you next time."). Google is therefore the primary
engine; Whisper (no prompt, strict hallucination filtering) is the offline fallback
and is loaded lazily so it costs no RAM while Google works.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thank you for watching.", "you", ".", "bye.", "bye",
    "subtitles by the amara.org community", "thank you very much.", "we'll see you next time.",
    "we'll see you in the next video.", "see you in the next video.", "and we'll be right back.", "okay.",
    "i'll see you next time.", "please subscribe.", "thanks for watching.",
}
_WAKE_WORD = r"(?:jarvis|jervis|jarvas|jarves|javis|jarvish|jarbis|garvis|harvis|travis|service|jarvi|chavis|davis)"
_LEAD = r"(?:hey|hi|hay|ok|okay|yo|a|we're|were|where|they|he)"
_WAKE_PREFIX = re.compile(r"^\s*(?:" + _LEAD + r"[,\s]+)?" + _WAKE_WORD + r"\b[\s,.!?:;-]*", re.I)
# distinctive spellings may appear anywhere; common words ("service", "davis") only count at the start
_WAKE_ANYWHERE = re.compile(r"\b(?:jarvis|jervis|jarvas|jarves|javis|jarvish|jarbis|garvis|harvis|chavis)\b", re.I)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", text or "")


def wake_similarity(text: str) -> float:
    """How much the start of a transcript sounds like "(hey) jarvis", 0..1 (spelling-level fuzzy match)."""
    words = _words(text)[:4]
    best = 0.0
    for n in (1, 2, 3):
        for start in range(0, max(1, min(2, len(words) - n + 1))):
            chunk = _letters("".join(words[start:start + n]))
            if chunk:
                for target in ("jarvis", "heyjarvis"):
                    best = max(best, difflib.SequenceMatcher(None, chunk, target).ratio())
    return best


def _alias_prefix_len(text: str, aliases: list[str]) -> int:
    """Number of leading words matching one of the user's learned wake phrases (0 = no match)."""
    words = _words(text)
    for alias in aliases:
        aw = _words(alias)
        if not aw:
            continue
        for extra in (0, 1, -1):  # recognisers sometimes merge/split a word
            n = len(aw) + extra
            if n < 1 or n > len(words):
                continue
            ratio = difflib.SequenceMatcher(None, _letters("".join(words[:n])), _letters(alias)).ratio()
            if ratio >= 0.8:
                return n
    return 0


def has_wake_word(text: str, aliases: list[str] | None = None, fuzzy: float = 0.72) -> bool:
    """True if a transcript contains "Jarvis", a known mis-hearing, or starts with the user's learned wake phrase."""
    text = text or ""
    if _WAKE_ANYWHERE.search(text) or _WAKE_PREFIX.match(text):
        return True
    if aliases and _alias_prefix_len(text, aliases):
        return True
    return wake_similarity(text) >= fuzzy


def strip_wake_word(text: str, aliases: list[str] | None = None) -> str:
    stripped = _WAKE_PREFIX.sub("", text).strip()
    if stripped != text.strip():
        return stripped
    n = _alias_prefix_len(text, aliases or [])
    if n:
        return " ".join(_words(text)[n:]) if n < len(_words(text)) else ""
    return text.strip()


def clean_transcript(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if t.lower() in _HALLUCINATIONS or not re.search(r"[A-Za-z0-9]", t):
        return ""
    words = t.lower().split()
    if len(words) >= 4 and max(words.count(w) for w in set(words)) / len(words) > 0.5:
        return ""  # repetition loop ("PowerShell, PowerShell, PowerShell, ...")
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
            segments, _ = self.model.transcribe(
                audio.astype(np.float32), language="en", beam_size=1, best_of=1, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
            )
            parts, logprobs = [], []
            for s in segments:
                if s.no_speech_prob > 0.6 or s.avg_logprob < -1.0 or s.compression_ratio > 2.4:
                    continue  # likely hallucinated on unclear audio
                parts.append(s.text)
                logprobs.append(s.avg_logprob)
            text = clean_transcript(" ".join(parts))
            conf = float(np.exp(np.mean(logprobs))) if logprobs else 0.0
            log.debug("whisper %.2fs audio -> %.2fs: %r", audio.shape[0] / 16000, time.time() - t, text)
            return text, conf


class GoogleSTT:
    name = "google"

    def __init__(self, language: str = "en-IN") -> None:
        self.language = language

    def transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        import speech_recognition as sr

        peak = float(np.abs(audio).max()) or 1.0
        gain = min(4.0, 0.6 / peak)  # normalise quiet speech without clipping
        pcm = (np.clip(audio * gain, -1, 1) * 32767).astype(np.int16).tobytes()
        r = sr.Recognizer()
        r.operation_timeout = 8
        data = sr.AudioData(pcm, 16000, 2)
        try:
            return clean_transcript(r.recognize_google(data, language=self.language)), 0.85
        except sr.UnknownValueError:
            return "", 0.0
        except sr.RequestError as exc:  # offline / blocked -> caller falls back to Whisper
            raise ConnectionError(f"Google speech service unreachable: {exc}") from exc


class STT:
    """Primary engine with automatic fallback when the primary is unreachable or fails."""

    def __init__(self, models: Path, backend: str = "google", model_size: str = "base.en",
                 language: str = "en-IN") -> None:
        self.models = models
        self.model_size = model_size
        self.backend = backend
        self.google = GoogleSTT(language)
        self._whisper: WhisperSTT | None = None
        self._whisper_failed = ""
        self.last_engine = ""
        if backend == "whisper":
            self._load_whisper()

    def _load_whisper(self) -> WhisperSTT | None:
        if self._whisper is None and not self._whisper_failed:
            try:
                self._whisper = WhisperSTT(self.models, self.model_size)
            except Exception as exc:  # noqa: BLE001
                self._whisper_failed = str(exc)
                log.warning("local Whisper unavailable: %s", exc)
        return self._whisper

    @property
    def primary(self) -> str:
        return self.backend

    def transcribe(self, audio: np.ndarray) -> tuple[str, float, str]:
        order = ["google", "whisper"] if self.backend == "google" else ["whisper", "google"]
        last: Exception | None = None
        for name in order:
            eng = self.google if name == "google" else self._load_whisper()
            if eng is None:
                continue
            try:
                text, conf = eng.transcribe(audio)
                self.last_engine = name
                return text, conf, name
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("STT engine %s failed: %s", name, exc)
        raise RuntimeError(f"all speech recognition engines failed: {last}")

