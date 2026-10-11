"""Microphone capture: one persistent stream, resampled to 16 kHz mono frames."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from jarvis.voice.devices import Device
from jarvis.voice.tts import resample

log = logging.getLogger(__name__)
TARGET = 16000
FRAME = 512


class MicCapture:
    def __init__(self, on_level: Callable[[float], None] | None = None) -> None:
        self.on_level = on_level
        self.device: Device | None = None
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)
        self._stream: sd.InputStream | None = None
        self._rate = TARGET
        self._carry = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self.error: str = ""
        self.overflows = 0

    def open(self, device: Device | None) -> None:
        self.close()
        self.device = device
        idx = device.index if device else None
        rate = device.samplerate if device else TARGET
        channels = 1
        errors = []
        for try_rate, try_ch in ((rate, 1), (rate, min(2, device.channels if device else 1)), (TARGET, 1),
                                 (44100, 1), (48000, 1)):
            try:
                self._stream = sd.InputStream(device=idx, samplerate=try_rate, channels=try_ch, dtype="float32",
                                              blocksize=int(try_rate * 0.02), callback=self._callback)
                self._rate, channels = try_rate, try_ch
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{try_rate}Hz/{try_ch}ch: {exc}")
        if self._stream is None:
            self.error = "; ".join(errors)
            raise RuntimeError(f"could not open microphone {device.name if device else 'default'}: {self.error}")
        self._stream.start()
        self.error = ""
        log.info("microphone open: %s (%d Hz, %d ch)", device.name if device else "default", self._rate, channels)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:  # noqa: BLE001
                    pass
                self._stream = None
            self._carry = np.zeros(0, dtype=np.float32)

    @property
    def active(self) -> bool:
        return self._stream is not None and self._stream.active

    def drain(self) -> None:
        try:
            while True:
                self.frames.get_nowait()
        except queue.Empty:
            pass

    def _callback(self, indata: np.ndarray, frames: int, _time: object, status: sd.CallbackFlags) -> None:
        if status.input_overflow:
            self.overflows += 1
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        mono = resample(mono, self._rate, TARGET) if self._rate != TARGET else mono.astype(np.float32)
        data = np.concatenate([self._carry, mono])
        n = data.shape[0] // FRAME
        for i in range(n):
            frame = data[i * FRAME : (i + 1) * FRAME].copy()
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                    self.frames.put_nowait(frame)
                except queue.Empty:
                    pass
        self._carry = data[n * FRAME :]
        if self.on_level is not None:
            self.on_level(float(np.sqrt(np.mean(mono**2))))
