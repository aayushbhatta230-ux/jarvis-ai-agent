"""Real-time speaker recognition (voice verification) of the enrolled user.

Uses the WeSpeaker ResNet34 speaker-embedding model (ONNX) with Kaldi-style
80-bin log-mel filterbank features computed in NumPy. The user enrols a few
phrases; their averaged, normalised embedding is the voiceprint. Every
utterance is embedded and compared by cosine similarity.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
MODEL_NAME = "wespeaker_en_voxceleb_resnet34.onnx"


@lru_cache(maxsize=1)
def _mel_banks(num_bins: int = 80, n_fft: int = 512, sr: int = 16000, low: float = 20.0,
               high: float = 0.0) -> np.ndarray:
    high = high if high > 0 else sr / 2 + high
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731
    mel_low, mel_high = mel(low), mel(high)
    delta = (mel_high - mel_low) / (num_bins + 1)
    fft_bins = n_fft // 2
    freqs = np.arange(fft_bins) * sr / n_fft
    mels = mel(freqs)
    banks = np.zeros((num_bins, fft_bins), dtype=np.float32)
    for b in range(num_bins):
        left, center, right = mel_low + b * delta, mel_low + (b + 1) * delta, mel_low + (b + 2) * delta
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        banks[b] = np.maximum(0.0, np.minimum(up, down))
    return banks


def fbank(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Kaldi-compatible fbank (hamming window, 25 ms / 10 ms, preemph 0.97, no dither)."""
    x = audio.astype(np.float64) * 32768.0
    flen, shift = int(0.025 * sr), int(0.010 * sr)
    if x.shape[0] < flen:
        x = np.pad(x, (0, flen - x.shape[0]))
    n = 1 + (x.shape[0] - flen) // shift
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    pre = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    window = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))
    spec = np.abs(np.fft.rfft(pre * window, n=512)) ** 2
    energies = spec[:, :256] @ _mel_banks().T
    return np.log(np.maximum(energies, np.finfo(np.float32).eps)).astype(np.float32)


@dataclass
class Verification:
    score: float
    accepted: bool
    enrolled: bool
    name: str = ""

    def label(self) -> str:
        if not self.enrolled:
            return "voice not enrolled"
        return f"{self.name} ({self.score:.2f})" if self.accepted else f"unknown speaker ({self.score:.2f})"


class SpeakerRecognizer:
    def __init__(self, models: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(str(models / MODEL_NAME), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.voiceprint: np.ndarray | None = None
        self.name = ""
        self.threshold = 0.55

    def embed(self, audio: np.ndarray) -> np.ndarray:
        feats = fbank(audio)
        feats = feats - feats.mean(axis=0, keepdims=True)  # cepstral mean normalisation
        emb = self.session.run(None, {"feats": feats[None, :, :]})[0][0]
        return emb / (np.linalg.norm(emb) + 1e-9)

    def set_voiceprint(self, embedding: np.ndarray, name: str, threshold: float) -> None:
        self.voiceprint = embedding / (np.linalg.norm(embedding) + 1e-9)
        self.name = name
        self.threshold = threshold

    @property
    def enrolled(self) -> bool:
        return self.voiceprint is not None

    def verify(self, audio: np.ndarray) -> Verification:
        if self.voiceprint is None:
            return Verification(0.0, True, False)
        score = float(self.embed(audio) @ self.voiceprint)
        seconds = audio.shape[0] / 16000
        # Very short clips give noisier embeddings: relax the bar slightly.
        threshold = self.threshold - (0.08 if seconds < 1.2 else 0.0)
        return Verification(score, score >= threshold, True, self.name)

    @staticmethod
    def build_voiceprint(embeddings: list[np.ndarray]) -> tuple[np.ndarray, float, float]:
        """Average enrolment embeddings; return (voiceprint, consistency, suggested threshold)."""
        mat = np.stack(embeddings)
        vp = mat.mean(axis=0)
        vp /= np.linalg.norm(vp) + 1e-9
        sims = mat @ vp
        consistency = float(sims.mean())
        threshold = float(np.clip(sims.min() - 0.12, 0.38, 0.62))
        return vp, consistency, threshold


class VoiceprintStore:
    """Persists voiceprints in the database."""

    def __init__(self, db: object) -> None:
        self.db = db

    def save(self, name: str, embedding: np.ndarray, samples: int, threshold: float) -> None:
        self.db.execute("DELETE FROM voiceprints WHERE name=?", (name,))  # type: ignore[attr-defined]
        self.db.execute("INSERT INTO voiceprints(name, embedding, samples, model, created) VALUES (?,?,?,?,?)",  # type: ignore[attr-defined]
                        (name, embedding.astype(np.float32).tobytes(), samples, MODEL_NAME, time.time()))
        self.db.kv_set("voiceprint_threshold", threshold)  # type: ignore[attr-defined]

    def load(self) -> tuple[str, np.ndarray, float] | None:
        row = self.db.one("SELECT name, embedding FROM voiceprints WHERE model=? ORDER BY id DESC LIMIT 1",  # type: ignore[attr-defined]
                          (MODEL_NAME,))
        if not row:
            return None
        return row["name"], np.frombuffer(row["embedding"], dtype=np.float32).copy(), \
            float(self.db.kv_get("voiceprint_threshold", 0.55))  # type: ignore[attr-defined]

    def delete(self) -> None:
        self.db.execute("DELETE FROM voiceprints")  # type: ignore[attr-defined]
