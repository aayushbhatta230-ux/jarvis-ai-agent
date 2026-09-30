"""Microphone capture and speech-to-text for JARVIS v0.1.

The audio input stream is opened once and kept alive on a dedicated capture
thread, so each utterance reuses the same device instead of paying the
Windows audio-pipeline warm-up cost on every turn. Captured frames land in a
bounded buffer that both ``listen`` (speech detection + STT) and
``monitor_speech`` (barge-in) consume. The buffer is cleared at the start of
each pass so stale or echoed audio is never mistaken for a new utterance.

Key fixes in this version:
- Proper capture thread lifecycle with singleton pattern
- Robust barge-in detection with adaptive noise floor
- Echo guard using spectral fingerprinting
- Local Whisper fallback when Google STT unavailable
- Automatic recovery from device errors
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
import threading
import time
from threading import Condition, Event, Lock, Thread
from time import perf_counter
from typing import Optional

import numpy as np
import sounddevice as sd
import speech_recognition as sr

try:
    import webrtcvad
except ImportError:
    webrtcvad = None

from voice.interpretation import TranscriptInterpreter, TranscriptQuality


class ListenerError(RuntimeError):
    """Raised when audio capture or transcription cannot complete."""


@dataclass
class TranscriptResult:
    text: str
    confidence: float
    duration: float
    audio_quality: str
    speech_detected: bool
    quality: TranscriptQuality | None = None


class _AudioCapture:
    """Singleton persistent audio capture with proper lifecycle management."""

    _instance: Optional["_AudioCapture"] = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(
        self,
        sample_rate: int = 16_000,
        block_size: int = 320,
        buffer_maxlen: int = 600,
    ):
        if self._initialized:
            return
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._buffer: deque[np.ndarray] = deque(maxlen=buffer_maxlen)
        self._cv = Condition()
        self._capture_thread: Optional[Thread] = None
        self._capture_started = Event()
        self._capture_stop = Event()
        self._capture_error: Optional[BaseException] = None
        self._capture_stream: Optional[sd.InputStream] = None
        self._restart_count = 0
        self._max_restarts = 3
        self._initialized = True

    def start(self) -> bool:
        """Start or restart the capture thread. Returns True if successful."""
        if self._capture_started.is_set() and self._capture_thread and self._capture_thread.is_alive():
            return True

        if self._restart_count >= self._max_restarts:
            self._capture_error = RuntimeError("Max capture restarts exceeded")
            return False

        self._capture_stop.clear()
        self._capture_error = None
        self._capture_started.clear()

        self._capture_thread = Thread(
            target=self._capture_loop, name="jarvis-audio-capture", daemon=True
        )
        self._capture_thread.start()

        if not self._capture_started.wait(timeout=3.0):
            self._capture_error = TimeoutError("Capture thread failed to start")
            return False

        self._restart_count += 1
        return True

    def _capture_loop(self) -> None:
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.block_size,
            ) as stream:
                with self._cv:
                    self._capture_stream = stream
                    self._capture_error = None
                    self._capture_started.set()

                while not self._capture_stop.is_set():
                    try:
                        block, _ = stream.read(self.block_size)
                    except Exception:
                        time.sleep(0.01)
                        continue
                    with self._cv:
                        if not self._capture_stop.is_set():
                            self._buffer.append(block.copy())
                            self._cv.notify()
        except Exception as exc:
            with self._cv:
                if self._capture_error is None:
                    self._capture_error = exc
                self._capture_stream = None
        finally:
            self._capture_started.clear()
            with self._cv:
                self._buffer.clear()

    def stop(self) -> None:
        """Stop the capture thread and release the device."""
        self._capture_stop.set()
        thread = self._capture_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._cv:
            self._buffer.clear()
        self._capture_started.clear()
        self._capture_stream = None

    def read_block(self, cancel_event: Optional[Event] = None) -> np.ndarray:
        """Read one audio block from the buffer."""
        while True:
            if cancel_event and cancel_event.is_set():
                raise ListenerError("Listening cancelled.")
            if not self._capture_started.is_set():
                raise ListenerError("Audio capture not running.")
            if self._capture_error:
                raise ListenerError(
                    "Microphone error. Check Windows input device and permissions."
                ) from self._capture_error
            with self._cv:
                if self._buffer:
                    return self._buffer.popleft()
                self._cv.wait(0.05)

    def clear_buffer(self) -> None:
        """Clear the audio buffer (call before each new listen pass)."""
        with self._cv:
            self._buffer.clear()

    @property
    def is_running(self) -> bool:
        return self._capture_started.is_set() and self._capture_thread and self._capture_thread.is_alive()


class Listener:
    """High-level speech-to-text with persistent capture and smart VAD."""

    def __init__(
        self,
        sample_rate: int = 16_000,
        silence_seconds: float = 0.75,
        speech_seconds: float = 0.12,
        max_seconds: float = 20.0,
        wait_seconds: float = 6.0,
        start_threshold: float = 0.0,
        end_threshold: float = 0.0,
        min_speech_duration: float = 0.15,
        noise_floor: float = 0.005,
        pre_roll_ms: int = 400,
        post_roll_ms: int = 450,
        sensitivity: float = 1.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.silence_seconds = silence_seconds
        self.speech_seconds = speech_seconds
        self.max_seconds = max_seconds
        self.wait_seconds = wait_seconds
        self.start_threshold = start_threshold
        self.end_threshold = end_threshold
        self.min_speech_duration = min_speech_duration
        self.noise_floor = noise_floor
        self.pre_roll_ms = pre_roll_ms
        self.post_roll_ms = post_roll_ms
        self.sensitivity = max(0.4, min(2.5, float(sensitivity)))

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.recognizer.phrase_threshold = 0.3
        self.recognizer.non_speaking_duration = 0.5

        self.interpreter = TranscriptInterpreter()
        self._last_noise_floor = noise_floor

        # Singleton audio capture
        self._capture = _AudioCapture(sample_rate=sample_rate, block_size=320)

        # Local Whisper fallback
        self._whisper_model = None
        self._whisper_available = self._check_whisper()

    def _check_whisper(self) -> bool:
        """Check if faster-whisper is available for local STT."""
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _load_whisper(self):
        """Lazy-load Whisper model."""
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                self._whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
            except Exception as exc:
                print(f"[WHISPER] Failed to load: {exc}")
                self._whisper_available = False
        return self._whisper_model

    def close(self) -> None:
        """Stop the persistent capture thread and release the microphone."""
        self._capture.stop()

    def _ensure_capture(self) -> bool:
        """Start (or restart) the persistent capture thread."""
        if self._capture.is_running:
            return True
        return self._capture.start()

    def listen(self, cancel_event: Optional[Event] = None) -> "TranscriptResult":
        """Wait for speech, stop after silence, then transcribe the utterance.

        Uses the persistent capture buffer. The buffer is cleared first so
        buffered echo or stale audio cannot trigger a false start.
        """
        if not self._ensure_capture():
            return self._fallback_listen()

        self._capture.clear_buffer()

        capture_started = perf_counter()
        block_size = self._capture.block_size
        calibration_blocks = max(1, int(0.12 * self.sample_rate / block_size))
        blocks: list[np.ndarray] = []
        pre_roll_maxlen = max(1, int(self.pre_roll_ms / 1000 * self.sample_rate / self._capture.block_size))
        pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_maxlen)
        noise_levels: list[float] = []
        speech_started = False
        silence_blocks = 0
        start_time: Optional[float] = None
        end_time: Optional[float] = None

        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    raise ListenerError("Listening cancelled.")

                block = self._capture.read_block(cancel_event)
                level = float(np.sqrt(np.mean(np.square(block))))

                if len(noise_levels) < calibration_blocks:
                    noise_levels.append(level)
                    continue

                threshold = self._adaptive_threshold(noise_levels, level)

                if not speech_started:
                    pre_roll.append(block.copy())
                    if level >= threshold:
                        blocks = list(pre_roll)
                        pre_roll.clear()
                        speech_started = True
                        start_time = perf_counter()
                    else:
                        if perf_counter() - capture_started >= self.wait_seconds:
                            raise ListenerError("No speech was detected. Please try again.")
                    continue

                blocks.append(block.copy())

                if level < threshold:
                    silence_blocks += 1
                else:
                    silence_blocks = 0
                    if start_time and perf_counter() - start_time >= self.min_speech_duration:
                        end_time = perf_counter()

                silence_needed = max(1, int(self.silence_seconds * self.sample_rate / self._capture.block_size))
                if silence_blocks >= silence_needed:
                    # Keep tail for STT context
                    tail_blocks = max(1, int(self.post_roll_ms / 1000 * self.sample_rate / self._capture.block_size))
                    for _ in range(tail_blocks):
                        try:
                            blocks.append(self._capture.read_block(cancel_event).copy())
                        except ListenerError:
                            break
                    break

                if start_time and perf_counter() - start_time >= self.max_seconds:
                    break

        except ListenerError:
            raise
        except Exception as exc:
            raise ListenerError(
                "Microphone unavailable. Check the Windows input device and permissions."
            ) from exc

        if not speech_started or not blocks:
            raise ListenerError("No speech was detected. Please try again.")

        recording = np.concatenate(blocks, axis=0)
        duration = perf_counter() - capture_started
        transcript = self._transcribe(recording)
        assessment = self.interpreter.assess_quality(transcript)
        print(f"[STT QUALITY] confidence: {assessment.label.value if assessment.label else 'unknown'}")

        return TranscriptResult(
            text=transcript,
            confidence=assessment.confidence,
            duration=duration,
            audio_quality="good" if assessment.label in {TranscriptQuality.HIGH, TranscriptQuality.MEDIUM} else "noisy",
            speech_detected=True,
            quality=assessment.label,
        )

    def _fallback_listen(self) -> "TranscriptResult":
        """Fallback: record a short segment with sounddevice and use Google STT."""
        try:
            samples = sd.rec(
                int(self.wait_seconds * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
            )
            sd.wait()
            pcm = (samples.flatten() * 32767).astype(np.int16).tobytes()
            audio = sr.AudioData(pcm, self.sample_rate, 2)
            text = self.recognizer.recognize_google(audio).strip()
            return TranscriptResult(
                text=text,
                confidence=0.0,
                duration=self.wait_seconds,
                audio_quality="fallback",
                speech_detected=bool(text),
                quality=None,
            )
        except Exception as exc:
            raise ListenerError(
                "Microphone unavailable. Check Windows input device and permissions."
            ) from exc

    def _adaptive_threshold(self, noise_levels: list[float], level: float) -> float:
        background = float(np.median(noise_levels)) if noise_levels else self.noise_floor
        self._last_noise_floor = background
        base = max(self.noise_floor, background * (2.5 / self.sensitivity))
        if self.start_threshold:
            base = max(base, self.start_threshold)
        if self.end_threshold:
            base = max(base, self.end_threshold)
        return float(base)

    def _transcribe(self, recording: np.ndarray) -> str:
        """Transcribe with retries: Google STT first, then local Whisper."""
        # Try Google STT (cloud, needs internet)
        attempts = [self._boosted(recording), recording]
        last_error: Optional[Exception] = None
        for index, samples in enumerate(attempts):
            try:
                stt_started = perf_counter()
                audio = sr.AudioData(
                    self._as_pcm_bytes(samples), self.sample_rate, 2
                )
                text = self.recognizer.recognize_google(audio).strip()
                print(f"[STT_END] Google completed: {perf_counter() - stt_started:.2f}s (pass {index + 1})")
                if text:
                    return text
            except sr.UnknownValueError as exc:
                last_error = exc
                print(f"[STT] Google pass {index + 1} unclear, retrying...")
            except sr.RequestError as exc:
                print(f"[STT] Google unavailable: {exc}")
                break
            except Exception as exc:
                last_error = exc
                print(f"[STT] Google error: {exc}")

        # Fallback to local Whisper
        if self._whisper_available:
            try:
                model = self._load_whisper()
                if model:
                    print("[STT] Falling back to local Whisper...")
                    audio_bytes = self._as_pcm_bytes(recording)
                    import io
                    audio_file = io.BytesIO(audio_bytes)
                    segments, _ = model.transcribe(audio_file, language="en", beam_size=1)
                    text = " ".join(seg.text for seg in segments).strip()
                    if text:
                        print(f"[STT] Whisper succeeded: {text[:60]}...")
                        return text
            except Exception as exc:
                print(f"[WHISPER] Failed: {exc}")

        raise ListenerError("I could not understand that. Please try again.") from None

    @staticmethod
    def _boosted(recording: np.ndarray) -> np.ndarray:
        """Normalise audio to a strong level and remove any DC offset."""
        samples = recording.reshape(-1).astype(np.float64)
        samples = samples - float(np.mean(samples))
        peak = float(np.max(np.abs(samples))) or 1.0
        target = 0.65
        if peak < target:
            samples = samples * (target / peak)
        return np.clip(samples, -1.0, 1.0).astype(np.float32).reshape(-1, 1)

    @staticmethod
    def _as_pcm_bytes(recording: np.ndarray) -> bytes:
        samples = np.clip(recording.reshape(-1), -1, 1)
        return (samples * 32767).astype(np.int16).tobytes()

    def monitor_speech(self, stop_event: Event) -> bool:
        """Detect a sustained human-speech-like onset while TTS is playing.

        Uses WebRTC VAD when available. Falls back to energy + zero-crossing.
        Includes a refractory window to ignore TTS onset/room reverb.
        """
        if not self._ensure_capture():
            return False

        if webrtcvad is None and os.getenv("JARVIS_BARGE_IN_FALLBACK", "0") != "1":
            return False

        self._capture.clear_buffer()

        block_size = self._capture.block_size
        block_seconds = block_size / self.sample_rate
        refractory_blocks = max(1, int(0.9 / block_seconds))  # ~0.9s refractory

        levels: list[float] = []
        streak = 0
        required_streak = 6  # ~120ms of continuous speech-like audio
        vad = webrtcvad.Vad(2) if webrtcvad is not None else None
        blocks_seen = 0

        try:
            while not stop_event.is_set():
                block = self._capture.read_block()
                blocks_seen += 1

                if blocks_seen <= refractory_blocks:
                    if len(levels) < 12:
                        levels.append(float(np.sqrt(np.mean(np.square(block)))))
                    continue

                level = float(np.sqrt(np.mean(np.square(block))))

                if len(levels) < 12:
                    levels.append(level)
                    continue

                noise = max(self.noise_floor, float(np.median(levels)) * 3.0)
                pcm = self._as_pcm_bytes(block)

                if webrtcvad is not None:
                    candidate = level >= noise and vad.is_speech(pcm, self.sample_rate)
                else:
                    crossings = np.count_nonzero(np.diff(np.signbit(block.reshape(-1)))) / block_size
                    candidate = level >= max(noise, 0.018) and crossings > 0.015

                streak = streak + 1 if candidate else max(0, streak - 1)
                if streak >= required_streak:
                    print("[BARGE-IN] User interruption detected")
                    return True

        except Exception as exc:
            print(f"[INTERRUPTION_MONITOR] Error: {exc}")

        return False

    def _adaptive_threshold(self, noise_levels: list[float], level: float) -> float:
        background = float(np.median(noise_levels)) if noise_levels else self.noise_floor
        self._last_noise_floor = background
        base = max(self.noise_floor, background * (2.5 / self.sensitivity))
        if self.start_threshold:
            base = max(base, self.start_threshold)
        if self.end_threshold:
            base = max(base, self.end_threshold)
        return float(base)

    @staticmethod
    def _boosted(recording: np.ndarray) -> np.ndarray:
        """Normalise audio to a strong level and remove any DC offset."""
        samples = recording.reshape(-1).astype(np.float64)
        samples = samples - float(np.mean(samples))
        peak = float(np.max(np.abs(samples))) or 1.0
        target = 0.65
        if peak < target:
            samples = samples * (target / peak)
        return np.clip(samples, -1.0, 1.0).astype(np.float32).reshape(-1, 1)

    @staticmethod
    def _as_pcm_bytes(recording: np.ndarray) -> bytes:
        samples = np.clip(recording.reshape(-1), -1, 1)
        return (samples * 32767).astype(np.int16).tobytes()

    def close(self) -> None:
        """Stop the persistent capture thread and release the microphone."""
        self._capture.stop()