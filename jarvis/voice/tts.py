"""Streaming, interruptible text-to-speech.

Text arrives in pieces (as the model streams), is split into sentences,
synthesised sentence-by-sentence (Edge neural voices, or offline Windows SAPI)
and played through a sounddevice output stream on the selected device. ``stop``
silences playback within one audio block (~20 ms) and drops queued sentences.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from jarvis.voice.devices import Device

log = logging.getLogger(__name__)
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+|\n{1,}")


def speakable(text: str) -> str:
    text = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)
    text = re.sub(r"[#*_>|~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or x.size == 0:
        return x.astype(np.float32)
    n = int(round(x.shape[0] * dst / src))
    return np.interp(np.linspace(0, x.shape[0] - 1, n), np.arange(x.shape[0]), x).astype(np.float32)


# --------------------------------------------------------------------- player
class Player:
    BLOCK = 2048  # larger blocks + "high" latency: no stutter under CPU load / Bluetooth

    def __init__(self, on_level: Callable[[float], None] | None = None) -> None:
        self.on_level = on_level
        self.volume = 1.0
        self.device: Device | None = None
        self.rate = 48000
        self._buf: deque[np.ndarray] = deque()
        self._cur = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self.idle = threading.Event()
        self.idle.set()

    def open(self, device: Device | None) -> None:
        self.close()
        self.device = device
        idx = device.index if device else None
        rate = device.samplerate if device else 48000
        try:
            self._stream = sd.OutputStream(device=idx, samplerate=rate, channels=1, dtype="float32",
                                           blocksize=self.BLOCK, callback=self._callback, latency="high")
        except Exception as exc:  # noqa: BLE001 - some WASAPI devices need 2 channels or their own rate
            log.warning("output open failed (%s); retrying stereo/default", exc)
            self._stream = sd.OutputStream(device=idx, samplerate=rate, channels=2, dtype="float32",
                                           blocksize=self.BLOCK, callback=self._callback)
        self.rate = int(self._stream.samplerate)
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None

    def write(self, pcm: np.ndarray, src_rate: int) -> None:
        data = resample(pcm, src_rate, self.rate) * self.volume
        with self._lock:
            self._buf.append(data)
            self.idle.clear()

    def stop(self) -> None:
        with self._lock:
            self._buf.clear()
            self._cur = np.zeros(0, dtype=np.float32)
            self.idle.set()

    def _callback(self, outdata: np.ndarray, frames: int, _time: object, status: sd.CallbackFlags) -> None:
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames:
                if self._cur.size == 0:
                    if not self._buf:
                        break
                    self._cur = self._buf.popleft()
                take = min(frames - filled, self._cur.size)
                out[filled : filled + take] = self._cur[:take]
                self._cur = self._cur[take:]
                filled += take
            if filled < frames and not self._buf and self._cur.size == 0:
                self.idle.set()
        if outdata.shape[1] == 1:
            outdata[:, 0] = out
        else:
            outdata[:] = out[:, None]
        if self.on_level is not None:
            self.on_level(float(np.sqrt(np.mean(out**2))) if filled else 0.0)


# ---------------------------------------------------------------- synthesizers
class EdgeSynth:
    name = "edge"
    rate = 24000

    def __init__(self, voice: str, rate_pct: int = 0) -> None:
        import edge_tts  # noqa: F401 - fail early if missing

        import miniaudio  # noqa: F401

        self.voice = voice
        self.rate_pct = rate_pct
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="jarvis-edge-tts", daemon=True).start()

    async def _synth(self, text: str) -> bytes:
        import edge_tts

        rate = f"{'+' if self.rate_pct >= 0 else ''}{int(self.rate_pct)}%"
        comm = edge_tts.Communicate(text, self.voice, rate=rate)
        chunks = []
        async for ch in comm.stream():
            if ch["type"] == "audio":
                chunks.append(ch["data"])
        return b"".join(chunks)

    def synth(self, text: str) -> np.ndarray:
        import miniaudio

        mp3 = asyncio.run_coroutine_threadsafe(self._synth(text), self._loop).result(timeout=20)
        if not mp3:
            raise RuntimeError("edge-tts returned no audio")
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=self.rate)
        return np.frombuffer(dec.samples, dtype=np.int16).astype(np.float32) / 32768.0


class SapiSynth:
    """Offline Windows voices. COM objects live on the synth worker thread."""

    name = "sapi"
    rate = 22050

    def __init__(self, voice_hint: str = "", rate_pct: int = 0) -> None:
        self.voice_hint = voice_hint
        self.rate_pct = rate_pct
        self._local = threading.local()  # COM objects must stay on the thread that created them

    @property
    def _engine(self):  # noqa: ANN202
        return getattr(self._local, "engine", None)

    def _ensure(self) -> None:
        if self._engine is not None:
            return
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        eng = win32com.client.Dispatch("SAPI.SpVoice")
        voices = eng.GetVoices()
        prefs = [self.voice_hint.lower()] if self.voice_hint else []
        prefs += ["ryan", "george", "david", "mark", "guy"]
        for p in prefs:
            for i in range(voices.Count):
                if p and p in voices.Item(i).GetDescription().lower():
                    eng.Voice = voices.Item(i)
                    break
            else:
                continue
            break
        eng.Rate = int(max(-10, min(10, self.rate_pct / 10)))
        self._local.engine = eng

    def synth(self, text: str) -> np.ndarray:
        import win32com.client

        self._ensure()
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 22  # SAFT22kHz16BitMono
        stream.Format = fmt
        self._engine.AudioOutputStream = stream  # type: ignore[union-attr]
        self._engine.Speak(text)  # type: ignore[union-attr]
        data = bytes(stream.GetData())
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


# --------------------------------------------------------------------- speaker
class Speaker:
    def __init__(self, backend: str = "edge", voice: str = "en-GB-RyanNeural", rate_pct: int = 0,
                 volume_pct: int = 100, on_level: Callable[[float], None] | None = None,
                 on_state: Callable[[bool], None] | None = None) -> None:
        self.player = Player(on_level)
        self.player.volume = max(0.0, min(1.0, volume_pct / 100))
        self.on_state = on_state
        self.primary = None
        if backend == "edge":
            try:
                self.primary = EdgeSynth(voice, rate_pct)
            except Exception as exc:  # noqa: BLE001
                log.warning("Edge TTS unavailable (%s); using Windows voices", exc)
        self.fallback = SapiSynth(rate_pct=rate_pct)
        self._sentences: queue.Queue[tuple[int, str]] = queue.Queue()
        self._gen = 0
        self._partial = ""
        self._busy = threading.Event()
        self.last_engine = ""
        self._edge_failures = 0
        self._cache: dict[str, tuple[np.ndarray, int]] = {}
        threading.Thread(target=self._worker, name="jarvis-tts", daemon=True).start()
        threading.Thread(target=self.precache, name="jarvis-tts-cache", daemon=True).start()

    # ------------------------------------------------------------ public api
    def open(self, device: Device | None) -> None:
        self.player.open(device)

    @property
    def speaking(self) -> bool:
        return self._busy.is_set() or not self._sentences.empty() or not self.player.idle.is_set()

    def say(self, text: str) -> None:
        self.feed(text)
        self.flush()

    def feed(self, piece: str) -> None:
        """Feed streamed text; complete sentences are queued for synthesis immediately."""
        self._partial += piece
        while True:
            m = _SENTENCE_END.search(self._partial)
            if not m:
                # long clause without punctuation: break at a comma to keep latency low
                if len(self._partial) > 220 and "," in self._partial[80:]:
                    cut = self._partial.index(",", 80) + 1
                    self._queue(self._partial[:cut])
                    self._partial = self._partial[cut:]
                    continue
                break
            sentence, self._partial = self._partial[: m.end()], self._partial[m.end():]
            self._queue(sentence)

    def flush(self) -> None:
        if self._partial.strip():
            self._queue(self._partial)
        self._partial = ""

    def stop(self) -> None:
        self._gen += 1
        self._partial = ""
        try:
            while True:
                self._sentences.get_nowait()
        except queue.Empty:
            pass
        self.player.stop()

    def wait_idle(self, timeout: float = 60.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not self.speaking:
                return True
            time.sleep(0.05)
        return False

    def close(self) -> None:
        self.stop()
        self.player.close()

    # -------------------------------------------------------------- internals
    def _queue(self, text: str) -> None:
        clean = speakable(text)
        if clean and re.search(r"\w", clean):
            self._sentences.put((self._gen, clean))
            if self.on_state:
                self.on_state(True)

    CACHED_PHRASES = ("On it.", "Right away.", "Working on it.", "Give me a moment.", "Certainly.", "Approved.",
                      "Understood, I won't do that.", "Yes?", "Listening.")

    def precache(self) -> None:
        """Synthesise short stock phrases ahead of time so they play with no network delay."""
        for phrase in self.CACHED_PHRASES:
            if phrase not in self._cache:
                try:
                    self._cache[phrase] = self._synth_uncached(phrase)
                except Exception:  # noqa: BLE001
                    break

    def _synth(self, text: str) -> tuple[np.ndarray, int]:
        hit = self._cache.get(text.strip())
        if hit is not None:
            return hit
        return self._synth_uncached(text)

    def _synth_uncached(self, text: str) -> tuple[np.ndarray, int]:
        if self.primary is not None and self._edge_failures < 3:
            try:
                pcm = self.primary.synth(text)
                self._edge_failures = 0
                self.last_engine = "edge"
                return pcm, self.primary.rate
            except Exception as exc:  # noqa: BLE001 - offline or service hiccup: fall back
                self._edge_failures += 1
                log.warning("edge-tts failed (%s); falling back to Windows voice", exc)
        self.last_engine = "sapi"
        return self.fallback.synth(text), self.fallback.rate

    def _worker(self) -> None:
        while True:
            gen, text = self._sentences.get()
            if gen != self._gen:
                continue
            self._busy.set()
            try:
                pcm, rate = self._synth(text)
                if gen == self._gen:
                    self.player.write(pcm, rate)
            except Exception:  # noqa: BLE001
                log.exception("speech synthesis failed")
            finally:
                self._busy.clear()
            if self._sentences.empty() and self.on_state:
                threading.Thread(target=self._notify_when_idle, args=(gen,), daemon=True).start()

    def _notify_when_idle(self, gen: int) -> None:
        self.player.idle.wait(120)
        time.sleep(0.05)
        if gen == self._gen and not self.speaking and self.on_state:
            self.on_state(False)
        elif gen != self._gen and self.on_state and not self.speaking:
            self.on_state(False)
