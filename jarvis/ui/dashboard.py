"""Holographic command-centre dashboard.

The humanoid hologram stands in the centre; floating glass panels around it show
live data: system gauges, a terminal of the agent's tool activity, the real-time
event/log feed, the conversation, the current task plan, a system monitor with
sparklines and top processes, and quick-action shortcuts. Everything updates in
real time from the event bus, Python logging and psutil polling (heavier probes
run on worker threads so the UI never stalls).
"""

from __future__ import annotations

import html
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import psutil
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui import theme
from jarvis.ui.avatar import HologramWidget
from jarvis.ui.chat import ConversationView
from jarvis.ui.orb import WaveformWidget

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
PURPLE = "#8B6CFF"


# ------------------------------------------------------------------ primitives
class GlassPanel(QFrame):
    def __init__(self, title: str, accent: str = theme.CYAN) -> None:
        super().__init__()
        self.setObjectName("Glass")
        self.setStyleSheet(
            "QFrame#Glass { background: rgba(10, 18, 30, 175); border: 1px solid rgba(63, 216, 255, 55);"
            " border-radius: 12px; }"
            "QFrame#Glass QLabel { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel(title.upper())
        t.setStyleSheet(f"color: {theme.TEXT}; font-size: 10px; font-weight: 700; letter-spacing: 2px;")
        head.addWidget(t)
        head.addStretch(1)
        for c in (theme.FAINT, theme.FAINT, accent):
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {c}; font-size: 7px;")
            head.addWidget(dot)
        lay.addLayout(head)
        self.body = lay


class RingGauge(QWidget):
    def __init__(self, label: str, color: str) -> None:
        super().__init__()
        self.label = label
        self.color = QColor(color)
        self.value = 0.0
        self.shown = 0.0
        self.sub = ""
        self.setMinimumSize(64, 84)

    def set_value(self, pct: float, sub: str = "") -> None:
        self.value = max(0.0, min(100.0, pct))
        self.sub = sub

    def step(self) -> None:
        self.shown += (self.value - self.shown) * 0.25
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        d = min(w - 8, self.height() - 30)
        r = QRectF((w - d) / 2, 16, d, d)
        p.setPen(QPen(QColor(theme.LINE), 5))
        p.drawEllipse(r)
        pen = QPen(self.color, 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(-self.shown / 100 * 360 * 16))
        p.setPen(QColor(theme.DIM))
        f = QFont()
        f.setPixelSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignmentFlag.AlignLeft, self.label)
        p.setPen(QColor(theme.TEXT))
        f.setPixelSize(max(10, int(d / 4.2)))
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{self.shown:.0f}%")
        f.setPixelSize(9)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(0, r.bottom() + 2, w, 14), Qt.AlignmentFlag.AlignCenter, self.sub)
        p.end()


class Sparkline(QWidget):
    def __init__(self, label: str, color: str, unit: str = "%", max_value: float | None = 100.0) -> None:
        super().__init__()
        self.label = label
        self.color = QColor(color)
        self.unit = unit
        self.max_value = max_value
        self.values: deque[float] = deque([0.0] * 60, maxlen=60)
        self.setMinimumHeight(52)

    def push(self, v: float) -> None:
        self.values.append(v)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignmentFlag.AlignLeft, self.label)
        cur = self.values[-1]
        f.setPixelSize(17)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.color)
        txt = f"{cur:.0f}{self.unit}" if self.unit == "%" else f"{cur:.1f} {self.unit}"
        p.drawText(QRectF(0, 12, w * 0.45, h - 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        top = self.max_value or max(1e-6, max(self.values) * 1.2)
        x0, gw, y0, gh = w * 0.45, w * 0.55, 14, h - 18
        path = QPainterPath()
        n = len(self.values)
        for i, v in enumerate(self.values):
            pt = QPointF(x0 + gw * i / (n - 1), y0 + gh - gh * min(1.0, v / top))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(x0 + gw, y0 + gh)
        fill.lineTo(x0, y0 + gh)
        grad = QLinearGradient(0, y0, 0, y0 + gh)
        c = QColor(self.color)
        c.setAlpha(90)
        grad.setColorAt(0, c)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillPath(fill, grad)
        p.setPen(QPen(self.color, 1.6))
        p.drawPath(path)
        p.end()


class _LogBridge(QObject):
    record = Signal(str, str, str)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: _LogBridge) -> None:
        super().__init__(logging.INFO)
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.record.emit(record.levelname, record.name.replace("jarvis.", ""), record.getMessage()[:240])
        except RuntimeError:
            pass  # widget already destroyed during shutdown


class _Probe(QObject):
    done = Signal(object)


# ------------------------------------------------------------------ dashboard
class HoloDashboard(QWidget):
    def __init__(self, app: Jarvis, avatar: str, send: Callable[[str], None],
                 open_tab: Callable[[str], None], listen: Callable[[], None]) -> None:
        super().__init__()
        self.app = app
        self.send_cb = send
        self.open_tab = open_tab
        self.current_task: str | None = None
        self._net_last = psutil.net_io_counters()
        self._net_t = time.time()
        self._gpu: dict[str, Any] = {}
        self._probe_busy = False
        mono = f"font-family: {theme.MONO}; font-size: 11px;"
        text_box = (f"QPlainTextEdit {{ background: rgba(3, 8, 14, 160); border: none; border-radius: 8px; {mono} "
                    f"color: #9FE8FF; padding: 4px; }}")

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 6, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)

        # ---------- left column
        overview = GlassPanel("System overview")
        row = QHBoxLayout()
        self.g_cpu = RingGauge("CPU", theme.CYAN)
        self.g_ram = RingGauge("RAM", PURPLE)
        self.g_disk = RingGauge("DISK", theme.BLUE)
        self.g_gpu = RingGauge("GPU", theme.GREEN)
        for g in (self.g_cpu, self.g_ram, self.g_disk, self.g_gpu):
            row.addWidget(g)
        overview.body.addLayout(row)
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setMaximumBlockCount(500)
        self.terminal.setStyleSheet(text_box)
        self.terminal.setPlainText("jarvis@agent:~$ ready\n")
        term = GlassPanel("Terminal · agent tools")
        term.body.addWidget(self.terminal)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(400)
        self.logs.setStyleSheet(text_box.replace("#9FE8FF", theme.TEXT))
        logs = GlassPanel("Real-time logs")
        logs.body.addWidget(self.logs)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(overview, 0)
        left.addWidget(term, 3)
        left.addWidget(logs, 2)
        grid.addLayout(left, 0, 0)

        # ---------- centre: hologram
        centre = QVBoxLayout()
        centre.setSpacing(2)
        self.holo = HologramWidget(avatar, pedestal=True)
        self.holo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.holo.clicked.connect(listen)
        centre.addWidget(self.holo, 1)
        self.state_label = QLabel("Standing by")
        self.state_label.setObjectName("StateLabel")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        centre.addWidget(self.state_label)
        self.wave = WaveformWidget(bars=64)
        centre.addWidget(self.wave)
        self.status = QLabel("")
        self.status.setObjectName("Status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        centre.addWidget(self.status)
        self.listen_btn = QPushButton("🎙  Stop listening")
        self.listen_btn.setToolTip("Stop / start listening (Ctrl+Alt+M) - or say “stop listening” / “start listening”")
        self.listen_btn.setFixedWidth(190)
        self.listen_btn.clicked.connect(self._toggle_listening)
        centre.addWidget(self.listen_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        grid.addLayout(centre, 0, 1)

        # ---------- right column
        chat_panel = GlassPanel("JARVIS · conversation")
        self.chat = ConversationView()
        chat_panel.body.addWidget(self.chat, 1)
        inrow = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask JARVIS anything…")
        self.input.returnPressed.connect(self._send)
        go = QPushButton("➤")
        go.setFixedWidth(36)
        go.setObjectName("Primary")
        go.clicked.connect(self._send)
        stop = QPushButton("■")
        stop.setFixedWidth(32)
        stop.setToolTip("Stop (Ctrl+Alt+X)")
        stop.clicked.connect(self.app.stop_everything)
        inrow.addWidget(self.input)
        inrow.addWidget(go)
        inrow.addWidget(stop)
        chat_panel.body.addLayout(inrow)

        task_panel = GlassPanel("Current task", PURPLE)
        self.task_goal = QLabel("Idle - give me something to do.")
        self.task_goal.setWordWrap(True)
        self.task_goal.setStyleSheet("font-size: 12px; font-weight: 600;")
        task_panel.body.addWidget(self.task_goal)
        self.task_prog = QProgressBar()
        self.task_prog.setRange(0, 1)
        self.task_prog.setVisible(False)
        task_panel.body.addWidget(self.task_prog)
        self.task_plan = QListWidget()
        self.task_plan.setMaximumHeight(110)
        task_panel.body.addWidget(self.task_plan)
        self.task_step = QLabel("")
        self.task_step.setWordWrap(True)
        self.task_step.setStyleSheet(f"color: {theme.DIM}; {mono}")
        task_panel.body.addWidget(self.task_step)
        trow = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._pause)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Danger")
        cancel.clicked.connect(lambda: self.current_task and self.app.agent.cancel(self.current_task))
        trow.addWidget(self.pause_btn)
        trow.addWidget(cancel)
        trow.addStretch(1)
        task_panel.body.addLayout(trow)

        shortcuts = GlassPanel("Shortcuts")
        srow = QHBoxLayout()
        for icon, label, cb in (
            ("⟳", "Self-test", lambda: self.open_tab("system:selftest")),
            ("◎", "Screen", lambda: self.send_cb("What's on my screen right now? Point out anything wrong.")),
            ("☀", "Briefing", lambda: self.send_cb("Give me my daily briefing.")),
            ("🎤", "Mic test", self._mic_test),
            ("🗣", "Train wake", self._train_wake),
            ("⚙", "Settings", lambda: self.open_tab("settings")),
        ):
            b = QToolButton()
            b.setText(f"{icon}\n{label}")
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setStyleSheet(f"QToolButton {{ background: rgba(16, 28, 44, 200); border: 1px solid {theme.LINE};"
                            f" border-radius: 10px; padding: 6px 4px; font-size: 11px; color: {theme.TEXT}; }}"
                            f"QToolButton:hover {{ border-color: {theme.CYAN}; }}")
            b.setMinimumWidth(56)
            b.clicked.connect(cb)
            srow.addWidget(b)
        shortcuts.body.addLayout(srow)
        right = QVBoxLayout()
        right.setSpacing(12)
        live = GlassPanel("Live screen", theme.GREEN)
        lrow = QHBoxLayout()
        self.screen_preview = QLabel("Not sharing.\nSay “share my screen”.")
        self.screen_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_preview.setMinimumSize(170, 96)
        self.screen_preview.setStyleSheet(f"color: {theme.DIM}; font-size: 11px; background: rgba(3,8,14,160);"
                                          " border-radius: 8px;")
        lrow.addWidget(self.screen_preview, 1)
        self.share_btn = QPushButton("Share screen")
        self.share_btn.setCheckable(True)
        self.share_btn.setChecked(self.app.screen.sharing)
        self.share_btn.toggled.connect(self._toggle_share)
        lrow.addWidget(self.share_btn, 0, Qt.AlignmentFlag.AlignTop)
        live.body.addLayout(lrow)
        right.addWidget(chat_panel, 5)
        right.addWidget(task_panel, 2)
        right.addWidget(live, 2)
        right.addWidget(shortcuts, 0)
        grid.addLayout(right, 0, 2)

        # ---------- far right: monitor
        monitor = GlassPanel("System monitor", PURPLE)
        self.s_cpu = Sparkline("CPU usage", theme.CYAN)
        self.s_ram = Sparkline("RAM usage", PURPLE)
        self.s_gpu = Sparkline("GPU usage", theme.GREEN)
        self.s_net = Sparkline("Network", theme.BLUE, unit="MB/s", max_value=None)
        for s in (self.s_cpu, self.s_ram, self.s_gpu, self.s_net):
            monitor.body.addWidget(s)
        procs_title = QLabel("PROCESSES")
        procs_title.setObjectName("Section")
        monitor.body.addWidget(procs_title)
        self.procs = QListWidget()
        self.procs.setStyleSheet(f"QListWidget {{ {mono} }} QListWidget::item {{ padding: 2px; border: none; }}")
        monitor.body.addWidget(self.procs, 1)
        self.models_lbl = QLabel("")
        self.models_lbl.setWordWrap(True)
        self.models_lbl.setStyleSheet(f"color: {theme.DIM}; font-size: 10px;")
        monitor.body.addWidget(self.models_lbl)
        grid.addWidget(monitor, 0, 3)

        grid.setColumnStretch(0, 26)
        grid.setColumnStretch(1, 34)
        grid.setColumnStretch(2, 30)
        grid.setColumnStretch(3, 20)

        # ---------- live data
        self._bridge = _LogBridge()
        self._bridge.record.connect(self._log_line)
        self._handler = _QtLogHandler(self._bridge)
        logging.getLogger().addHandler(self._handler)
        self._probe = _Probe()
        self._probe.done.connect(self._apply_probe)
        self._fast = QTimer(self)
        self._fast.timeout.connect(self._tick_fast)
        self._fast.start(1000)
        self._anim = QTimer(self)
        self._anim.timeout.connect(lambda: [g.step() for g in (self.g_cpu, self.g_ram, self.g_disk, self.g_gpu)])
        self._anim.start(50)
        self._slow_n = 0
        psutil.cpu_percent(None)
        self._tick_fast()

    # --------------------------------------------------------------- backdrop
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bg = QRadialGradient(QPointF(w * 0.42, h * 0.45), max(w, h) * 0.75)
        bg.setColorAt(0, QColor(14, 24, 48))
        bg.setColorAt(0.5, QColor(8, 12, 26))
        bg.setColorAt(1, QColor(3, 5, 10))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 14, 14)
        p.fillPath(path, bg)
        # faint perspective grid floor
        p.setClipPath(path)
        grid = QColor(63, 120, 255, 18)
        p.setPen(QPen(grid, 1))
        horizon = h * 0.62
        for i in range(1, 14):
            y = horizon + (h - horizon) * (i / 13) ** 1.8
            p.drawLine(QPointF(0, y), QPointF(w, y))
        cx = w * 0.42
        for i in range(-14, 15):
            p.drawLine(QPointF(cx + i * 22, horizon), QPointF(cx + i * 160, h))
        # soft skyline glows
        for k, (x, col) in enumerate(((0.08, PURPLE), (0.86, theme.BLUE), (0.65, PURPLE))):
            g = QRadialGradient(QPointF(w * x, h * 0.2), w * 0.18)
            c = QColor(col)
            c.setAlpha(22 + 6 * k)
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), g)
        p.end()

    # ------------------------------------------------------------ live probes
    def _tick_fast(self) -> None:
        if not self.isVisible():
            return
        cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        now = time.time()
        net = psutil.net_io_counters()
        rate = ((net.bytes_recv - self._net_last.bytes_recv) + (net.bytes_sent - self._net_last.bytes_sent)) / \
            max(0.2, now - self._net_t) / 1e6
        self._net_last, self._net_t = net, now
        freq = psutil.cpu_freq()
        self.g_cpu.set_value(cpu, f"{freq.current / 1000:.1f} GHz" if freq else "")
        self.g_ram.set_value(vm.percent, f"{vm.used / 1e9:.1f}/{vm.total / 1e9:.1f} GB")
        self.s_cpu.push(cpu)
        self.s_ram.push(vm.percent)
        self.s_net.push(rate)
        gpu = self._gpu
        if gpu:
            self.g_gpu.set_value(gpu["vram_used_mb"] / max(1, gpu["vram_total_mb"]) * 100,
                                 f"VRAM {gpu['vram_used_mb'] / 1024:.1f}/{gpu['vram_total_mb'] / 1024:.0f} GB")
            self.s_gpu.push(gpu["util_pct"])
        self._slow_n += 1
        if self._slow_n % 3 == 1 and not self._probe_busy:
            self._probe_busy = True
            threading.Thread(target=self._slow_probe, daemon=True).start()

    def _slow_probe(self) -> None:
        data: dict[str, Any] = {}
        try:
            from jarvis.tools.system import gpu_info

            gpus = gpu_info()
            data["gpu"] = gpus[0] if gpus else {}
            du = psutil.disk_usage("C:\\")
            data["disk"] = (du.percent, du.used / 1e9, du.total / 1e9)
            procs = []
            for pr in psutil.process_iter(["name", "memory_info"]):
                mi = pr.info["memory_info"]
                if mi:
                    procs.append((mi.rss, pr.info["name"] or "?"))
            data["procs"] = sorted(procs, reverse=True)[:8]
            ollama = self.app.router.providers.get("ollama")
            if ollama is not None:
                try:
                    data["models"] = [m["name"] for m in ollama.running()]  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    data["models"] = []
        except Exception:  # noqa: BLE001
            log.debug("dashboard probe failed", exc_info=True)
        self._probe.done.emit(data)

    def _apply_probe(self, data: dict[str, Any]) -> None:
        self._probe_busy = False
        if data.get("gpu"):
            self._gpu = data["gpu"]
        if "disk" in data:
            pct, used, total = data["disk"]
            self.g_disk.set_value(pct, f"{used:.0f}/{total:.0f} GB")
        if "procs" in data:
            self.procs.clear()
            for rss, name in data["procs"]:
                self.procs.addItem(QListWidgetItem(f"{name[:22]:<22} {rss / 1e6:>6.0f} MB"))
        if "models" in data:
            self.models_lbl.setText("Loaded models: " + (", ".join(data["models"]) or "none (idle)"))

    # -------------------------------------------------------------- feeds
    def _log_line(self, level: str, name: str, msg: str) -> None:
        color = {"WARNING": theme.AMBER, "ERROR": theme.RED, "CRITICAL": theme.RED}.get(level, theme.CYAN)
        self.logs.appendHtml(f'<span style="color:{theme.DIM}">{time.strftime("%H:%M:%S")}</span> '
                             f'<span style="color:{color}">[{level[:4]}]</span> '
                             f'<span style="color:{theme.DIM}">{html.escape(name)}</span> {html.escape(msg)}')

    def _event_line(self, text: str, color: str = theme.CYAN) -> None:
        self.logs.appendHtml(f'<span style="color:{theme.DIM}">{time.strftime("%H:%M:%S")}</span> '
                             f'<span style="color:{color}">●</span> {html.escape(text)}')

    def on_event(self, t: str, d: dict[str, Any]) -> None:
        if t == "assistant.state":
            self.holo.set_state(d["state"])
            self.state_label.setText(theme.STATE_LABEL.get(d["state"], d["state"]))
        elif t == "audio.level":
            self.holo.set_level(d["level"], d["source"])
            self.wave.push(d["level"], theme.TEXT if d["source"] == "tts" else theme.CYAN)
        elif t == "user.message":
            self.chat.add_user(d["text"], d.get("source", "text"))
        elif t == "assistant.delta":
            self.chat.stream(d["text"])
        elif t == "assistant.message":
            self.chat.finish(d["text"], d.get("model", ""))
        elif t == "tool.start":
            self.terminal.appendPlainText(f"jarvis@agent:~$ {d['tool']} {d.get('args', '')[:160]}")
        elif t == "tool.end":
            mark = "✓" if d.get("ok") else "✕"
            extra = f" {d['error'][:160]}" if d.get("error") else ""
            self.terminal.appendPlainText(f"  {mark} {d['tool']} ({d.get('duration', 0)}s){extra}")
        elif t == "agent.step" and d.get("kind") == "tool":
            out = "\n".join("  │ " + ln for ln in d.get("result", "").splitlines()[:8])
            if out:
                self.terminal.appendPlainText(out)
            self.task_step.setText(f"{d.get('tool')}: {d.get('result', '')[:140]}")
        elif t == "agent.task":
            self.current_task = d["task_id"]
            self.task_goal.setText(d["goal"])
            self._plan(d.get("plan") or [])
            st = d["status"]
            self.pause_btn.setText("Resume" if st == "paused" else "Pause")
            if st in ("completed", "completed_unverified", "failed", "cancelled"):
                color = theme.GREEN if st.startswith("completed") else theme.RED if st == "failed" else theme.DIM
                self._event_line(f"task {st}: {d['goal'][:80]}", color)
                self.task_step.setText(st.replace("_", " "))
            else:
                self.task_step.setText(f"{st} · step {d['step']} · {d.get('model') or ''}")
        elif t == "agent.plan":
            self._plan(d["plan"])
        elif t in ("voice.wake", "voice.interrupted"):
            self._event_line("wake word" if t == "voice.wake" else "interrupted by you")
        elif t == "voice.speaker":
            self._event_line(f"voice {'recognised' if d['accepted'] else 'rejected'}: {d['label']}",
                             theme.GREEN if d["accepted"] else theme.AMBER)
        elif t == "voice.transcript" and d.get("text"):
            self._event_line(f"heard ({d['engine']}): {d['text'][:80]}")
        elif t == "model.call":
            self._event_line(f"model {d['candidate']} ({d['role']})", PURPLE)
        elif t == "model.fallback":
            self._event_line(f"fallback from {d['candidate']}: {d['error'][:80]}", theme.AMBER)
        elif t == "permission.request":
            self._event_line(f"approval needed: {d['summary'][:90]}", theme.AMBER)
        elif t in ("notify", "screen.alert"):
            self._event_line(f"{d.get('title', d.get('window', ''))}: {d.get('message', ' '.join(d.get('lines', [])))[:90]}",
                             theme.AMBER)
        elif t == "voice.listening":
            self.listen_btn.setText("🎙  Stop listening" if d["on"] else "💤  Start listening")
            self.listen_btn.setObjectName("" if d["on"] else "Primary")
            self.listen_btn.style().unpolish(self.listen_btn)
            self.listen_btn.style().polish(self.listen_btn)
            self._event_line("listening " + ("on" if d["on"] else "paused - say “start listening”"), theme.CYAN)
        elif t == "screen.frame":
            from PySide6.QtGui import QPixmap

            pm = QPixmap()
            if pm.loadFromData(d["jpeg"], "JPEG"):
                self.screen_preview.setPixmap(pm.scaled(self.screen_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation))
        elif t == "screen.sharing":
            self.share_btn.blockSignals(True)
            self.share_btn.setChecked(d["on"])
            self.share_btn.setText("Stop sharing" if d["on"] else "Share screen")
            self.share_btn.blockSignals(False)
            if not d["on"]:
                self.screen_preview.clear()
                self.screen_preview.setText("Not sharing.\nSay “share my screen”.")
            self._event_line("screen sharing " + ("on - I can see your screen" if d["on"] else "off"), theme.GREEN)
        elif t == "voice.resumed":
            self._event_line(f"small interruption (“{d.get('heard') or '…'}”) - carrying on")
        elif t == "voice.enroll":
            self.chat.add_system(f"({d['step']}/{d['total']})  Say: “{d['phrase']}”")
            self.state_label.setText(f"Say “{d['phrase']}”  ({d['step']}/{d['total']})")
        elif t == "voice.mic_check":
            verdict = "✓ Microphone OK" if d["ok"] else "⚠ Microphone needs attention"
            lines = [f"{verdict} — {d['device']}",
                     f"peak {d['peak']}, speech level {d['speech_level']}, noise floor {d['noise_floor']}, "
                     f"{'wideband' if d['wideband'] else 'Bluetooth telephone-band'} audio",
                     f"Heard ({d['engine']}): “{d['heard'] or '—'}”"] + list(d.get("notes", []))
            self.chat.add_system("\n".join(lines))
        elif t == "memory.added":
            self._event_line(f"remembered: {d['content'][:80]}", theme.GREEN)

    def _plan(self, plan: list[dict[str, Any]]) -> None:
        self.task_plan.clear()
        done = 0
        for s in plan:
            mark = {"done": "✓", "active": "▶", "failed": "✕", "skipped": "–"}.get(s.get("status"), "○")
            it = QListWidgetItem(f"{mark} {s.get('title', '')}")
            if s.get("status") == "done":
                done += 1
                it.setForeground(QColor(theme.DIM))
            self.task_plan.addItem(it)
        self.task_prog.setRange(0, max(1, len(plan)))
        self.task_prog.setValue(done)
        self.task_prog.setVisible(bool(plan))

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    # --------------------------------------------------------------- actions
    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.send_cb(text)

    def _pause(self) -> None:
        run = self.app.agent.runs.get(self.current_task or "")
        if not run:
            return
        if run.status == "paused":
            self.app.agent.unpause(run.id)
        else:
            self.app.agent.pause(run.id)

    def _toggle_listening(self) -> None:
        voice = self.app.voice
        if voice is None:
            self.chat.add_system("Voice isn't running.")
            return
        threading.Thread(target=self.app.set_listening, args=(voice.sleeping, "ui"), daemon=True).start()

    def _toggle_share(self, on: bool) -> None:
        self.share_btn.setText("Stop sharing" if on else "Share screen")
        threading.Thread(target=self.app.screen.set_sharing, args=(on,), daemon=True).start()

    def _mic_test(self) -> None:
        voice = self.app.voice
        if voice is None:
            self.chat.add_system("Voice isn't running yet.")
            return
        self.chat.add_system("Mic test: speak a normal sentence for the next 5 seconds…")

        def work() -> None:
            try:
                voice.mic_check(5.0)
            except Exception as exc:  # noqa: BLE001
                self.app.events.publish("notice", text=f"Mic test failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _train_wake(self) -> None:
        voice = self.app.voice
        if voice is None:
            self.chat.add_system("Voice isn't running yet.")
            return
        self.chat.add_system("Wake-word training: say “Hey JARVIS” each time you're prompted, naturally, "
                             "with a short pause after.")

        def work() -> None:
            try:
                res = voice.train_wake(5)
                heard = "; ".join(f"“{h or '—'}”" for h in res["heard"])
                self.app.events.publish("assistant.message", final=True, text=(
                    f"Wake-word training done. I heard: {heard}. "
                    f"I'll now recognise {res['recognised']}/{res['samples']} of those as you calling me."))
            except Exception as exc:  # noqa: BLE001
                self.app.events.publish("notice", text=f"Wake training failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _backup(self) -> None:
        def work() -> None:
            try:
                path = self.app.db.backup()
                self.app.events.publish("notice", text=f"Backup written: {path.name}")
            except Exception as exc:  # noqa: BLE001
                self.app.events.publish("notice", text=f"Backup failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def close_feeds(self) -> None:
        logging.getLogger().removeHandler(self._handler)


