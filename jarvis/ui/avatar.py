"""Holographic humanoid avatar.

A 3D model (OBJ) is sampled into a dense surface point cloud once, then each
frame is rotated, projected and splatted into a float buffer with NumPy, glowed
(cheap separable blur) and tinted by the assistant's state. The jaw region
moves with JARVIS's voice level (lip-sync), the head turns gently, a scan line
sweeps while working, and a projector pedestal emits light at the base.
Rendering stays well under 5 ms/frame at the default size and stops entirely
while the window is hidden.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from jarvis.core import paths
from jarvis.ui import theme

log = logging.getLogger(__name__)


@dataclass
class AvatarSpec:
    file: str
    crop_min: float  # keep points above this fraction of model height (0 = whole model)
    mouth: float  # mouth height as a fraction of the cropped model height
    jaw_depth: float  # vertical extent of the jaw region (fraction)
    jaw_halfwidth: float  # half-width of the jaw region (fraction of model width)
    yaw_offset: float = 0.0


AVATARS: dict[str, AvatarSpec] = {
    "female": AvatarSpec("female02_clean.obj", crop_min=0.60, mouth=0.773, jaw_depth=0.055, jaw_halfwidth=0.11),
    "male": AvatarSpec("bust.obj", crop_min=0.0, mouth=0.39, jaw_depth=0.12, jaw_halfwidth=0.2),
}


def _load_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("v "):
                verts.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                idx = [int(p.split("/")[0]) for p in line.split()[1:]]
                for k in range(1, len(idx) - 1):  # fan-triangulate polygons
                    faces.append([idx[0], idx[k], idx[k + 1]])
    v = np.asarray(verts, dtype=np.float32)
    f = np.asarray(faces, dtype=np.int64)
    f = np.where(f < 0, f + len(v), f - 1)
    return v, f


def build_cloud(spec: AvatarSpec, n_points: int = 16000, seed: int = 3) -> np.ndarray:
    """Area-weighted surface sampling -> (N, 7): x, y, z, brightness, nx, ny, nz (height normalised to 1)."""
    cache = paths.models_dir() / f"avatar_{Path(spec.file).stem}_{n_points}_n.npy"
    if cache.exists():
        return np.load(cache)
    v, f = _load_obj(paths.PROJECT_ROOT / spec.file)
    y0, y1 = v[:, 1].min(), v[:, 1].max()
    cut = y0 + spec.crop_min * (y1 - y0)
    f = f[v[f][:, :, 1].min(axis=1) >= cut]
    tri = v[f]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = 0.5 * np.linalg.norm(fn, axis=1)
    fn = fn / (np.linalg.norm(fn, axis=1, keepdims=True) + 1e-9)
    vn = np.zeros_like(v)  # smooth vertex normals for the crisp feature points
    for k in range(3):
        np.add.at(vn, f[:, k], fn * area[:, None])
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-9
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(f), size=n_points, p=area / area.sum())
    r1, r2 = rng.random(n_points), rng.random(n_points)
    s = np.sqrt(r1)
    w0, w1, w2 = (1 - s), s * (1 - r2), s * r2
    pts = tri[pick, 0] * w0[:, None] + tri[pick, 1] * w1[:, None] + tri[pick, 2] * w2[:, None]
    nrm = vn[f[pick, 0]] * w0[:, None] + vn[f[pick, 1]] * w1[:, None] + vn[f[pick, 2]] * w2[:, None]
    used = np.unique(f)
    pts = np.vstack([pts, v[used]])
    nrm = np.vstack([nrm, vn[used]])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    pts = (pts - (lo + hi) / 2) / (hi[1] - lo[1])
    bright = np.concatenate([np.full(n_points, 0.7, np.float32), np.full(len(used), 1.0, np.float32)])
    cloud = np.column_stack([pts, bright, nrm]).astype(np.float32)
    try:
        np.save(cache, cloud)
    except OSError:
        pass
    return cloud


def _blur(img: np.ndarray, radius: int) -> np.ndarray:
    """Fast box blur via cumulative sums (separable)."""
    if radius < 1:
        return img
    k = 2 * radius + 1
    pad = np.pad(img, ((radius + 1, radius), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0)
    img = (c[k:] - c[:-k]) / k
    pad = np.pad(img, ((0, 0), (radius + 1, radius)), mode="edge")
    c = np.cumsum(pad, axis=1)
    return (c[:, k:] - c[:, :-k]) / k


class HologramWidget(QWidget):
    """Drop-in replacement for the orb: same ``set_state`` / ``set_level`` / ``clicked`` API."""

    clicked = Signal()

    def __init__(self, avatar: str = "female", parent: QWidget | None = None, pedestal: bool = True) -> None:
        super().__init__(parent)
        self.setMinimumSize(160, 180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to talk (Ctrl+Alt+L)")
        self.pedestal = pedestal
        self.state = "idle"
        self._col = np.array(theme.qcolor(theme.STATE_STYLE["idle"][0]).getRgbF()[:3], np.float32)
        self._target = self._col.copy()
        self._speed = 0.25
        self._level = 0.0
        self._level_target = 0.0
        self._mic = 0.0
        self._mic_target = 0.0
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._flash = 0.0
        self._cloud: np.ndarray | None = None
        self._jaw_mask: np.ndarray | None = None
        self._spec = AVATARS.get(avatar, AVATARS["female"])
        self._load(avatar)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._frame: QImage | None = None
        self._buf_cache: np.ndarray | None = None

    # ------------------------------------------------------------------ setup
    def _load(self, avatar: str) -> None:
        try:
            self._spec = AVATARS.get(avatar, AVATARS["female"])
            cloud = build_cloud(self._spec)
            ys = cloud[:, 1] + 0.5  # 0 bottom .. 1 top
            mouth = self._spec.mouth
            front = cloud[:, 2] > np.percentile(cloud[:, 2], 40)
            jaw = (ys < mouth) & (ys > mouth - self._spec.jaw_depth) & \
                  (np.abs(cloud[:, 0]) < self._spec.jaw_halfwidth) & front
            weight = np.clip((mouth - ys) / self._spec.jaw_depth, 0, 1)
            self._jaw_mask = (jaw * (1 - np.abs(weight - 0.5) * 0.6)).astype(np.float32)
            self._cloud = cloud
        except Exception as exc:  # noqa: BLE001 - fall back to a procedural sphere of points
            log.warning("avatar model unavailable (%s); using procedural form", exc)
            rng = np.random.default_rng(0)
            p = rng.normal(size=(6000, 3))
            p /= np.linalg.norm(p, axis=1, keepdims=True) * 2.4
            self._cloud = np.column_stack([p, np.full(len(p), 0.8)]).astype(np.float32)
            self._jaw_mask = np.zeros(len(p), np.float32)

    def set_avatar(self, avatar: str) -> None:
        self._load(avatar)

    # ---------------------------------------------------------------- inputs
    def set_state(self, state: str) -> None:
        if state not in theme.STATE_STYLE:
            state = "idle"
        if state == "completed" and self.state != "completed":
            self._flash = 1.0
        self.state = state
        core, _glow, speed, _pulse = theme.STATE_STYLE[state]
        self._target = np.array(theme.qcolor(core).getRgbF()[:3], np.float32)
        self._speed = speed

    def set_level(self, level: float, source: str = "tts") -> None:
        if source == "tts":
            self._level_target = max(self._level_target, min(1.0, level))
        else:
            self._mic_target = max(self._mic_target, min(1.0, level))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    # --------------------------------------------------------------- render
    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.08, now - self._last)
        self._last = now
        self._col += (self._target - self._col) * min(1.0, dt * 4)
        self._level += (self._level_target - self._level) * min(1.0, dt * 18)
        self._level_target *= 0.8
        self._mic += (self._mic_target - self._mic) * min(1.0, dt * 12)
        self._mic_target *= 0.85
        self._flash = max(0.0, self._flash - dt)
        self._render()
        self.update()

    def _render(self) -> None:
        if self._cloud is None:
            return
        w, h = max(1, self.width()), max(1, self.height())
        scale_px = min(0.75, 420.0 / max(1, h))  # constant point density at any size; upscale smoothly
        bw, bh = max(8, int(w * scale_px)), max(8, int(h * scale_px))
        t = time.perf_counter() - self._t0
        c = self._cloud
        x, y, z = c[:, 0].copy(), c[:, 1].copy(), c[:, 2].copy()
        # lip-sync: open the jaw with the voice level
        if self._jaw_mask is not None and self._level > 0.01:
            open_amt = self._level * 0.022 * (0.7 + 0.3 * math.sin(t * 23))
            y -= self._jaw_mask * open_amt
            z += self._jaw_mask * open_amt * 0.3
        # gentle idle head motion, faster attention when listening/working
        yaw = 0.35 * math.sin(t * 0.35) + self._spec.yaw_offset
        if self.state == "listening":
            yaw *= 0.3  # face the user while listening
        pitch = 0.04 * math.sin(t * 0.5) - 0.05
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        xr = x * cy + z * sy
        zr = -x * sy + z * cy
        yr = y * cp - zr * sp
        zr = y * sp + zr * cp
        # rotate normals the same way, for Fresnel rim lighting
        nx, ny, nz = c[:, 4], c[:, 5], c[:, 6]
        nzr = -nx * sy + nz * cy
        nzr = ny * sp + nzr * cp
        facing = np.abs(nzr)
        rim = (1.0 - facing) ** 2
        # perspective projection, seated on the pedestal
        cam = 2.6
        f = 1.0 / (cam - zr)
        if self.pedestal:
            fit = min(bh * 1.72, bw * 2.4)
            base_y = bh * 0.49
        else:
            fit = min(bh * 2.2, bw * 2.6)
            base_y = bh * 0.5
        bob = 0.006 * math.sin(t * 1.3)
        px = (bw / 2 + xr * f * fit).astype(np.int32)
        py = (base_y - (yr + bob) * f * fit).astype(np.int32)
        inside = (px >= 0) & (px < bw) & (py >= 0) & (py < bh)
        front = np.clip(zr + 0.5, 0.0, 1.0)
        bands = 0.72 + 0.28 * np.sin(yr * 260.0 - t * 3.0)  # horizontal hologram scan bands
        intensity = c[:, 3] * (0.16 + 0.9 * rim + 0.2 * front) * bands
        buf = np.zeros((bh, bw), np.float32)
        np.add.at(buf, (py[inside], px[inside]), intensity[inside])
        # adaptive exposure: keep the brightest edges just below white
        nz_px = buf[buf > 0]
        ref = float(np.percentile(nz_px, 97)) if nz_px.size else 1.0
        buf /= max(ref, 1e-3)
        if self.state in ("processing", "executing", "analyzing_screen", "coding"):
            sl = int(((t * (0.35 + 0.15 * self._speed)) % 1.0) * bh)
            buf[max(0, sl - 2):sl + 2, :] *= 2.2
        glow = _blur(buf, max(1, bw // 60))
        lum = 1.0 - np.exp(-(buf * 1.1 + glow * (1.4 + 1.2 * self._level + 0.6 * self._mic)))
        if self._flash > 0:
            lum = np.clip(lum * (1.0 + self._flash * 0.6), 0, 1)
        col = self._col
        white = np.clip(lum - 0.82, 0, 0.18) / 0.18  # only the hottest edges bleach towards white
        rgb = np.empty((bh, bw, 4), np.uint8)
        for ch in range(3):
            rgb[:, :, 2 - ch] = np.clip((lum * col[ch] + white * 0.6 * (1 - col[ch])) * 255, 0, 255)
        rgb[:, :, 3] = np.clip(lum * 255, 0, 255)
        self._buf_cache = rgb
        self._frame = QImage(rgb.data, bw, bh, bw * 4, QImage.Format.Format_ARGB32)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        t = time.perf_counter() - self._t0
        col = QColor.fromRgbF(float(self._col[0]), float(self._col[1]), float(self._col[2]))
        # back glow
        halo = QRadialGradient(QPointF(w / 2, h * 0.42), min(w, h) * 0.6)
        hc = QColor(col)
        hc.setAlpha(int(46 + 40 * self._level + 30 * self._mic))
        halo.setColorAt(0, hc)
        halo.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), halo)
        if self.pedestal:
            self._paint_pedestal(p, w, h, col, t)
        if self._frame is not None:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.drawImage(QRectF(0, 0, w, h), self._frame)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # rising data particles
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(26):
            phase = (t * (0.08 + (i % 5) * 0.02) + i * 0.137) % 1.0
            xx = w / 2 + math.sin(i * 12.9898) * w * 0.28
            yy = h * (0.86 - phase * 0.75)
            pc = QColor(col)
            pc.setAlpha(int(160 * (1 - phase)))
            p.setBrush(pc)
            p.drawEllipse(QPointF(xx, yy), 1.3, 1.3)
        p.end()

    def _paint_pedestal(self, p: QPainter, w: int, h: int, col: QColor, t: float) -> None:
        cx, cy = w / 2, h * 0.9
        rx, ry = w * 0.36, h * 0.045
        # light cone from the projector
        cone = QLinearGradient(QPointF(cx, cy), QPointF(cx, h * 0.35))
        c0 = QColor(col)
        c0.setAlpha(int(70 + 60 * self._mic + 50 * self._level))
        cone.setColorAt(0, c0)
        cone.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cone)
        from PySide6.QtGui import QPolygonF

        p.drawPolygon(QPolygonF([QPointF(cx - rx * 0.9, cy), QPointF(cx + rx * 0.9, cy),
                                 QPointF(cx + rx * 0.5, h * 0.35), QPointF(cx - rx * 0.5, h * 0.35)]))
        # base disc
        disc = QRadialGradient(QPointF(cx, cy), rx)
        d0 = QColor(col)
        d0.setAlpha(150)
        disc.setColorAt(0, d0)
        disc.setColorAt(0.6, QColor(12, 30, 45, 200))
        disc.setColorAt(1, QColor(5, 10, 16, 230))
        p.setBrush(disc)
        p.drawEllipse(QPointF(cx, cy), rx, ry)
        # concentric rings, pulsing with the microphone (listening ripples)
        for i in range(4):
            k = ((t * 0.4 + i / 4) % 1.0)
            rc = QColor(col)
            rc.setAlpha(int((1 - k) * (110 + 120 * self._mic)))
            p.setPen(QPen(rc, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), rx * (0.25 + 0.75 * k), ry * (0.25 + 0.75 * k))
        rim = QColor(col)
        rim.setAlpha(220)
        p.setPen(QPen(rim, 2))
        p.drawEllipse(QPointF(cx, cy), rx, ry)
        if self._flash > 0:
            fc = QColor(theme.GREEN)
            fc.setAlpha(int(220 * self._flash))
            p.setPen(QPen(fc, 2.5))
            grow = 1 + (1 - self._flash) * 0.25
            p.drawEllipse(QPointF(cx, cy), rx * grow, ry * grow)
