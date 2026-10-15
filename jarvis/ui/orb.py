"""The animated JARVIS orb and the live audio waveform."""

from __future__ import annotations

import math
import random
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from jarvis.ui import theme


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


class OrbWidget(QWidget):
    """Layered holographic orb. Colour, rotation and pulse follow the assistant state;
    radius and ripples follow live audio level (mic while listening, voice while speaking)."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(140, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to talk (Ctrl+Alt+L)")
        self.state = "idle"
        self._core = theme.qcolor(theme.STATE_STYLE["idle"][0])
        self._glow = theme.qcolor(theme.STATE_STYLE["idle"][1])
        self._target_core = QColor(self._core)
        self._target_glow = QColor(self._glow)
        self._speed = 0.25
        self._pulse = 0.25
        self._level = 0.0
        self._level_target = 0.0
        self._phase = 0.0
        self._scan = 0.0
        self._flash = 0.0
        self._last = time.perf_counter()
        rnd = random.Random(7)
        self._particles = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(0.78, 1.18), rnd.uniform(0.4, 1.6),
                            rnd.uniform(0.6, 2.2)) for _ in range(90)]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ---------------------------------------------------------------- inputs
    def set_state(self, state: str) -> None:
        if state not in theme.STATE_STYLE:
            state = "idle"
        if state == "completed" and self.state != "completed":
            self._flash = 1.0
        self.state = state
        core, glow, speed, pulse = theme.STATE_STYLE[state]
        self._target_core = theme.qcolor(core)
        self._target_glow = theme.qcolor(glow)
        self._speed = speed
        self._pulse = pulse

    def set_level(self, level: float) -> None:
        self._level_target = max(self._level_target, min(1.0, level))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start(16)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()  # no rendering cost while hidden
        super().hideEvent(event)

    # ------------------------------------------------------------ animation
    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._core = _lerp(self._core, self._target_core, min(1.0, dt * 4))
        self._glow = _lerp(self._glow, self._target_glow, min(1.0, dt * 4))
        self._level += (self._level_target - self._level) * min(1.0, dt * 14)
        self._level_target *= 0.86
        self._phase += dt * self._speed
        self._scan = (self._scan + dt * 0.5) % 1.0
        self._flash = max(0.0, self._flash - dt * 1.2)
        # idle: drop to ~30 fps to keep CPU usage low
        idle = self.state in ("idle", "paused") and self._level < 0.02
        interval = 33 if idle else 16
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        c = QPointF(w / 2, h / 2)
        base = min(w, h) * 0.30
        t = self._phase
        breathe = 1.0 + 0.035 * math.sin(time.perf_counter() * (1.2 + self._pulse * 1.6)) * (0.4 + self._pulse)
        r = base * breathe * (1.0 + 0.22 * self._level)

        # outer glow
        glow = QRadialGradient(c, r * 2.1)
        g0 = QColor(self._glow)
        g0.setAlpha(150)
        g1 = QColor(self._glow)
        g1.setAlpha(40)
        glow.setColorAt(0.0, g0)
        glow.setColorAt(0.45, g1)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, r * 2.1, r * 2.1)

        # completion flash ring
        if self._flash > 0:
            fc = QColor(theme.GREEN)
            fc.setAlpha(int(200 * self._flash))
            p.setPen(QPen(fc, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = r * (1.2 + (1 - self._flash) * 0.9)
            p.drawEllipse(c, rr, rr)

        # particle halo
        p.setPen(Qt.PenStyle.NoPen)
        for ang, dist, size, spd in self._particles:
            a = ang + t * spd * 0.6
            d = r * (dist + 0.08 * math.sin(t * 2 + ang * 3) + 0.25 * self._level * math.sin(ang * 5 + t * 6))
            pc = QColor(self._core)
            pc.setAlpha(int(70 + 120 * (0.5 + 0.5 * math.sin(ang * 7 + t * 3))))
            p.setBrush(pc)
            p.drawEllipse(QPointF(c.x() + d * math.cos(a), c.y() + d * math.sin(a)), size, size)

        # rotating arcs
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, (rad, width, span, direction) in enumerate(((1.32, 1.6, 70, 1), (1.45, 1.0, 120, -1),
                                                           (1.58, 2.2, 36, 1))):
            col = QColor(self._core)
            col.setAlpha(150 - i * 35)
            pen = QPen(col, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            rr = r * rad
            rect = QRectF(c.x() - rr, c.y() - rr, rr * 2, rr * 2)
            start = int((direction * t * 90 * (1 + i * 0.4)) % 360 * 16)
            p.drawArc(rect, start, int(span * 16))
            p.drawArc(rect, start + 180 * 16, int(span * 0.6 * 16))

        # audio ripple ring (deforms with level)
        if self._level > 0.02:
            path = QPainterPath()
            steps = 96
            for k in range(steps + 1):
                a = 2 * math.pi * k / steps
                wob = 1.12 + self._level * 0.18 * (math.sin(a * 6 + t * 9) + 0.6 * math.sin(a * 11 - t * 7))
                pt = QPointF(c.x() + r * wob * math.cos(a), c.y() + r * wob * math.sin(a))
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            col = QColor(self._core)
            col.setAlpha(int(90 + 150 * min(1.0, self._level)))
            p.setPen(QPen(col, 1.4))
            p.drawPath(path)

        # core sphere
        core = QRadialGradient(QPointF(c.x() - r * 0.25, c.y() - r * 0.3), r * 1.25)
        hi = QColor("#FFFFFF")
        hi.setAlpha(150)
        mid = QColor(self._core)
        mid.setAlpha(200)
        edge = QColor(self._glow)
        edge.setAlpha(250)
        core.setColorAt(0.0, hi)
        core.setColorAt(0.35, mid)
        core.setColorAt(0.8, edge)
        core.setColorAt(1.0, QColor(2, 6, 10, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(c, r, r)

        # inner latitude lines (gives the sphere a holographic feel)
        p.save()
        clip = QPainterPath()
        clip.addEllipse(c, r, r)
        p.setClipPath(clip)
        line = QColor("#FFFFFF")
        line.setAlpha(28)
        p.setPen(QPen(line, 1))
        for k in range(-3, 4):
            yy = c.y() + k * r * 0.26 + math.sin(t * 2) * r * 0.05
            p.drawLine(QPointF(c.x() - r, yy), QPointF(c.x() + r, yy))
        if self.state == "analyzing_screen":
            sc = QColor(theme.CYAN)
            sc.setAlpha(160)
            p.setPen(QPen(sc, 2))
            yy = c.y() - r + 2 * r * self._scan
            p.drawLine(QPointF(c.x() - r, yy), QPointF(c.x() + r, yy))
        p.restore()
        p.end()


class WaveformWidget(QWidget):
    """Scrolling bar visualisation of recent audio levels."""

    def __init__(self, parent: QWidget | None = None, bars: int = 48) -> None:
        super().__init__(parent)
        self.setFixedHeight(26)
        self.levels: deque[float] = deque([0.0] * bars, maxlen=bars)
        self.color = QColor(theme.CYAN)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._decay)
        self._timer.start(40)
        self._pending = 0.0

    def push(self, level: float, color: str | None = None) -> None:
        self._pending = max(self._pending, min(1.0, level))
        if color:
            self.color = QColor(color)

    def _decay(self) -> None:
        self.levels.append(self._pending)
        self._pending *= 0.5
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.levels)
        w, h = self.width(), self.height()
        gap = 2.0
        bw = max(1.5, (w - gap * (n - 1)) / n)
        for i, lv in enumerate(self.levels):
            bh = max(2.0, lv * (h - 2))
            col = QColor(self.color)
            col.setAlpha(int(60 + 195 * (i / n)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            x = i * (bw + gap)
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, bw, bh), bw / 2, bw / 2)
        p.end()
