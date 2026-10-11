"""Voice activity detection: Silero VAD (ONNX) with an energy-based fallback."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
FRAME = 512  # samples per VAD frame at 16 kHz (32 ms)


class SileroVAD:
    CONTEXT = 64

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = frame.astype(np.float32).reshape(1, -1)
        inp = np.concatenate([self._context, x], axis=1)
        out, self._state = self.session.run(None, {"input": inp, "state": self._state,
                                                    "sr": np.array(16000, dtype=np.int64)})
        self._context = inp[:, -self.CONTEXT:]
        return float(out[0][0])


class EnergyVAD:
    """Adaptive-noise-floor fallback when the ONNX model is unavailable."""

    def __init__(self) -> None:
        self.floor = 0.003

    def reset(self) -> None:
        pass

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) + 1e-9)
        if rms < self.floor * 2:
            self.floor = 0.95 * self.floor + 0.05 * rms
        ratio = rms / max(self.floor, 1e-4)
        return float(np.clip((ratio - 2.0) / 4.0, 0.0, 1.0))


def load_vad(models: Path) -> SileroVAD | EnergyVAD:
    path = models / "silero_vad.onnx"
    if path.exists():
        try:
            return SileroVAD(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Silero VAD failed to load (%s); using energy VAD", exc)
    return EnergyVAD()
