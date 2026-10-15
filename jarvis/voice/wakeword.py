""""Hey JARVIS" wake-word detection with openWakeWord models via ONNX Runtime.

The openwakeword Python package cannot be imported on this machine (Smart App
Control blocks SciPy), so its streaming feature pipeline is re-implemented
here: melspectrogram -> 96-d speech embeddings -> wake-word classifier.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
CHUNK = 1280  # 80 ms at 16 kHz


class WakeWord:
    def __init__(self, models: Path, model_name: str = "hey_jarvis_v0.1.onnx") -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(models / "melspectrogram.onnx"), sess_options=opts, providers=prov)
        self.emb = ort.InferenceSession(str(models / "embedding_model.onnx"), sess_options=opts, providers=prov)
        self.clf = ort.InferenceSession(str(models / model_name), sess_options=opts, providers=prov)
        self._clf_input = self.clf.get_inputs()[0].name
        self._n_feat = int(self.clf.get_inputs()[0].shape[1])
        self.reset()

    def reset(self) -> None:
        self._raw = np.zeros(0, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._mel_buf = np.ones((76, 32), dtype=np.float32)
        rng = np.random.default_rng(0)
        noise = rng.integers(-1000, 1000, 16000 * 4).astype(np.float32)
        self._feat = self._embed_audio(noise)
        self._warmup = 5

    def _melspec(self, audio_i16: np.ndarray) -> np.ndarray:
        out = self.mel.run(None, {"input": audio_i16[None, :].astype(np.float32)})[0]
        return np.squeeze(out) / 10.0 + 2.0

    def _embed_window(self, mel76: np.ndarray) -> np.ndarray:
        return self.emb.run(None, {"input_1": mel76[None, :, :, None].astype(np.float32)})[0].reshape(-1, 96)

    def _embed_audio(self, audio_i16: np.ndarray) -> np.ndarray:
        spec = self._melspec(audio_i16)
        windows = [spec[i : i + 76] for i in range(0, spec.shape[0] - 75, 8)]
        return np.vstack([self._embed_window(w) for w in windows])

    def process(self, audio: np.ndarray) -> float:
        """Feed 16 kHz float audio in [-1, 1]; returns the highest score from complete 80 ms chunks."""
        self._pending = np.concatenate([self._pending, audio.astype(np.float32) * 32767.0])
        best = 0.0
        while self._pending.shape[0] >= CHUNK:
            chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
            self._raw = np.concatenate([self._raw, chunk])[-(16000 * 2):]
            if self._raw.shape[0] < CHUNK + 480:
                continue
            spec = self._melspec(self._raw[-(CHUNK + 480):])
            self._mel_buf = np.vstack([self._mel_buf, spec])[-970:]
            window = self._mel_buf[-76:]
            if window.shape[0] == 76:
                self._feat = np.vstack([self._feat, self._embed_window(window)])[-120:]
            score = float(self.clf.run(None, {self._clf_input: self._feat[-self._n_feat:][None, :, :]})[0].reshape(-1)[0])
            if self._warmup > 0:
                self._warmup -= 1
                score = 0.0
            best = max(best, score)
        return best


def load_wakeword(models: Path) -> WakeWord | None:
    needed = ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"]
    if not all((models / n).exists() for n in needed):
        log.warning("wake-word models missing in %s", models)
        return None
    try:
        return WakeWord(models)
    except Exception as exc:  # noqa: BLE001
        log.warning("wake-word model failed to load: %s", exc)
        return None
