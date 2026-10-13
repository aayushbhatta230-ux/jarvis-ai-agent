"""Main JARVIS window: orb-centred shell with compact, conversation and activity modes."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui import theme
from jarvis.ui.avatar import HologramWidget
from jarvis.ui.bridge import EventBridge
from jarvis.ui.dashboard import HoloDashboard
from jarvis.ui.chat import Card, ConversationView
from jarvis.ui.hotkeys import HotkeyManager
from jarvis.ui.orb import OrbWidget, WaveformWidget
from jarvis.ui.panels import ActivityPanel, MemoryPanel, SchedulePanel, SettingsPanel, SystemPanel

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
SIZES = {"compact": QSize(300, 420), "conversation": QSize(470, 800), "activity": QSize(1120, 800),
         "holo": QSize(1500, 860)}


def make_icon(color: str = theme.CYAN) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QRadialGradient(26, 24, 34)
    g.setColorAt(0, QColor("#FFFFFF"))
    g.setColorAt(0.35, QColor(color))
    g.setColorAt(1, QColor("#03131C"))
    p.setBrush(g)
    p.setPen(QColor(color))
    p.drawEllipse(QRectF(6, 6, 52, 52))
    p.end()
    return QIcon(pm)


class MainWindow(QWidget):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        self.mode = app.config.get("ui.start_mode", "compact")
        self._drag: QPoint | None = None
        self._cards: dict[str, Card] = {}
        self._speaker_label = ""
        self.setObjectName("Root")
        self.setWindowTitle("JARVIS")
        self.setWindowIcon(make_icon())
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if app.config.get("ui.always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(theme.STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.shell = QFrame()
        self.shell.setObjectName("Shell")
        outer.addWidget(self.shell)
        shell_lay = QVBoxLayout(self.shell)
        shell_lay.setContentsMargins(0, 10, 0, 0)
        shell_lay.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(16, 0, 16, 4)
        title = QLabel("J.A.R.V.I.S")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        self.mute_btn = self._icon_btn("🎙", "Mute / unmute microphone", self.toggle_mute, checkable=True)
        self.mode_btns = {
            "holo": self._icon_btn("◈", "Holographic command centre", lambda: self.set_mode("holo")),
            "compact": self._icon_btn("◉", "Compact (avatar only)", lambda: self.set_mode("compact")),
            "conversation": self._icon_btn("☰", "Conversation", lambda: self.set_mode("conversation")),
            "activity": self._icon_btn("▦", "Activity & settings", lambda: self.set_mode("activity")),
        }
        header.addWidget(self.mute_btn)
        for b in self.mode_btns.values():
            header.addWidget(b)
        header.addWidget(self._icon_btn("—", "Hide to tray", self.hide))
        shell_lay.addLayout(header)
        self.stack = QStackedWidget()
        shell_lay.addWidget(self.stack, 1)
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(16, 4, 16, 14)
        root.setSpacing(18)
        self.stack.addWidget(page)

        # ----- left column: avatar, status, conversation, input
        left = QVBoxLayout()
        left.setSpacing(6)
        self.avatar_name = app.config.get("ui.avatar", "female")
        self.orb = OrbWidget() if self.avatar_name == "orb" else HologramWidget(self.avatar_name, pedestal=False)
        self.orb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.orb.clicked.connect(self.listen)
        left.addWidget(self.orb, 5)
        self.state_label = QLabel("Starting up")
        self.state_label.setObjectName("StateLabel")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.state_label)
        self.wave = WaveformWidget()
        left.addWidget(self.wave)
        self.status = QLabel("")
        self.status.setObjectName("Status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        self.task_strip = QLabel("")
        self.task_strip.setObjectName("Muted")
        self.task_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.task_strip.setWordWrap(True)
        self.task_strip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.task_strip.mousePressEvent = lambda _e: self.set_mode("activity")  # type: ignore[method-assign]
        left.addWidget(self.task_strip)

        self.chat = ConversationView()
        left.addWidget(self.chat, 7)
        self.notice = QLabel("")
        self.notice.setObjectName("Muted")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        left.addWidget(self.notice)
        inrow = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask or tell JARVIS anything…   (Ctrl+Alt+K)")
        self.input.returnPressed.connect(self.send)
        inrow.addWidget(self.input)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setToolTip("Stop speaking and cancel running tasks (Ctrl+Alt+X)")
        self.stop_btn.setFixedWidth(38)
        self.stop_btn.clicked.connect(self.app.stop_everything)
        inrow.addWidget(self.stop_btn)
        self.input_row = QWidget()
        self.input_row.setLayout(inrow)
        left.addWidget(self.input_row)
        self.left_w = QWidget()
        self.left_w.setLayout(left)
        root.addWidget(self.left_w, 5)

        # ----- right column: panels
        self.tabs = QTabWidget()
        self.activity = ActivityPanel(app)
        self.memory = MemoryPanel(app)
        self.schedule = SchedulePanel(app)
        self.system = SystemPanel(app)
        self.settings = SettingsPanel(app)
        self.tabs.addTab(self.activity, "Activity")
        self.tabs.addTab(self.schedule, "Schedule")
        self.tabs.addTab(self.memory, "Memory")
        self.tabs.addTab(self.system, "System")
        self.tabs.addTab(self.settings, "Settings")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 7)

        # ----- holographic command centre
        self.dashboard = HoloDashboard(app, "female" if self.avatar_name == "orb" else self.avatar_name,
                                       send=self._send_text, open_tab=self._open_tab, listen=self.listen)
        self.stack.addWidget(self.dashboard)
        app.config.on_change(self._config_changed)

        # ----- events, tray, hotkeys
        self.bridge = EventBridge(app.events)
        self.bridge.event.connect(self.on_event)
        self._setup_tray()
        self.hotkeys = HotkeyManager()
        QApplication.instance().installNativeEventFilter(self.hotkeys)
        hk = app.config.get("ui.hotkeys", {})
        self.hotkey_labels: dict[str, str] = {}
        for action, default, alt, cb in (("summon", "ctrl+alt+j", "ctrl+shift+j", self.toggle_visible),
                                         ("listen", "ctrl+alt+l", "ctrl+shift+space", self.listen),
                                         ("command", "ctrl+alt+k", "ctrl+shift+k", self.focus_input),
                                         ("cancel", "ctrl+alt+x", "ctrl+shift+x", self.app.stop_everything),
                                         ("toggle_listening", "ctrl+alt+m", "ctrl+shift+m", self.toggle_listening)):
            for combo in (hk.get(action, default), default, alt):
                if self.hotkeys.register(combo, cb):
                    self.hotkey_labels[action] = combo
                    break
        missing = [a for a in ("summon", "listen", "command", "cancel", "toggle_listening")
                   if a not in self.hotkey_labels]
        self.hotkeys.failed = missing
        if self.hotkeys.failed:
            self.show_notice(f"Hotkeys unavailable (in use by another app): {', '.join(self.hotkeys.failed)}")
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(lambda: self._set_state("idle"))
        self.set_mode(self.mode, initial=True)
        self.chat.add_system("JARVIS is starting. Say “Hey JARVIS”, click the orb, or type below.")

    # ------------------------------------------------------------------ chrome
    def _icon_btn(self, text: str, tip: str, cb: Any, checkable: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("Icon")
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.clicked.connect(cb)
        return b

    def set_mode(self, mode: str, initial: bool = False) -> None:
        if mode not in SIZES:
            mode = "holo"
        self.mode = mode
        holo = mode == "holo"
        self.stack.setCurrentIndex(1 if holo else 0)
        on_top = mode == "compact" and bool(self.app.config.get("ui.always_on_top", True))
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on_top:
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            if visible:
                self.show()
        for m, b in self.mode_btns.items():
            b.setStyleSheet(f"color: {theme.CYAN};" if m == mode else "")
        compact = mode == "compact"
        self.chat.setVisible(not compact)
        self.input_row.setVisible(not compact)
        self.tabs.setVisible(mode == "activity")
        self.task_strip.setVisible(True)
        for m, b in self.mode_btns.items():
            b.setStyleSheet(f"color: {theme.CYAN};" if m == mode else "")
        size = QSize(SIZES[mode])
        avail = QApplication.primaryScreen().availableGeometry()
        size = size.boundedTo(QSize(avail.width() - 24, avail.height() - 24))
        if holo:
            self.resize(size)
            self.move(avail.x() + (avail.width() - size.width()) // 2,
                      avail.y() + (avail.height() - size.height()) // 2)
            self.dashboard.input.setFocus()
            return
        if initial:
            screen = QApplication.primaryScreen().availableGeometry()
            self.resize(size)
            self.move(screen.right() - size.width() - 24, screen.bottom() - size.height() - 24)
        else:
            geo = self.geometry()
            right, bottom = geo.right(), geo.bottom()
            self.resize(size)
            self.move(max(0, right - size.width()), max(0, bottom - size.height()))
        if not compact:
            self.input.setFocus()
        if mode == "activity":
            self._tab_changed(self.tabs.currentIndex())

    def _send_text(self, text: str) -> None:
        if self.app.voice is not None:
            self.app.voice.interrupt()
        self.app.submit_input(text, "text")

    def _open_tab(self, which: str) -> None:
        self.set_mode("activity")
        if which.startswith("system"):
            self.tabs.setCurrentWidget(self.system)
            if which.endswith("selftest"):
                self.system.self_test()
        else:
            self.tabs.setCurrentWidget(self.settings)

    def _config_changed(self, key: str, value: Any) -> None:
        if key == "ui.avatar" and value in ("female", "male"):
            self.dashboard.holo.set_avatar(value)
            if isinstance(self.orb, HologramWidget):
                self.orb.set_avatar(value)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            if self.app.busy() or (self.app.voice and self.app.voice.speaker.speaking):
                self.app.stop_everything()
            else:
                self.hide()
        super().keyPressEvent(e)

    def closeEvent(self, e) -> None:  # noqa: N802
        e.ignore()
        self.hide()
        self.tray.showMessage("JARVIS", "Still running in the tray. Ctrl+Alt+J to summon.", self.tray.icon(), 2500)

    def _tab_changed(self, idx: int) -> None:
        w = self.tabs.widget(idx)
        if w is self.memory:
            self.memory.refresh()
        elif w is self.schedule:
            self.schedule.refresh()
        elif w is self.system:
            self.system.refresh_audit()
        elif w is self.activity:
            self.activity.refresh()

    # -------------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("JARVIS")
        menu = QMenu()
        for text, cb in (("Show / hide", self.toggle_visible), ("Talk now", self.listen),
                         ("Type a command", self.focus_input), ("Stop everything", self.app.stop_everything)):
            act = QAction(text, menu)
            act.triggered.connect(cb)
            menu.addAction(act)
        self.listen_action = QAction("Stop listening  (Ctrl+Alt+M)", menu)
        self.listen_action.triggered.connect(self.toggle_listening)
        menu.addAction(self.listen_action)
        self.mute_action = QAction("Mute microphone", menu, checkable=True)
        self.mute_action.triggered.connect(lambda checked: self.set_muted(checked))
        menu.addAction(self.mute_action)
        menu.addSeparator()
        for text, cb in (("Activity", lambda: (self.show_window(), self.set_mode("activity"),
                                               self.tabs.setCurrentWidget(self.activity))),
                         ("Run self-test", lambda: (self.show_window(), self.set_mode("activity"),
                                                    self.tabs.setCurrentWidget(self.system), self.system.self_test())),
                         ("Settings", lambda: (self.show_window(), self.set_mode("activity"),
                                               self.tabs.setCurrentWidget(self.settings)))):
            act = QAction(text, menu)
            act.triggered.connect(cb)
            menu.addAction(act)
        menu.addSeparator()
        quit_act = QAction("Quit JARVIS", menu)
        quit_act.triggered.connect(self.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.toggle_visible()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def quit(self) -> None:
        self.dashboard.close_feeds()
        self.hotkeys.unregister_all()
        self.tray.hide()
        QApplication.instance().quit()

    # ----------------------------------------------------------------- actions
    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_visible(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_window()

    def listen(self) -> None:
        if self.app.voice is None:
            self.show_notice("Voice isn't available - type your request instead.")
            self.focus_input()
            return
        if self.app.voice.muted:
            self.set_muted(False)
        self.app.voice.push_to_talk()

    def toggle_listening(self) -> None:
        if self.app.voice is None:
            self.show_notice("Voice isn't running.")
            return
        on = self.app.voice.sleeping  # asleep -> start listening, and vice versa
        threading.Thread(target=self.app.set_listening, args=(on, "ui"), daemon=True).start()

    def focus_input(self) -> None:
        self.show_window()
        if self.mode == "holo":
            self.dashboard.input.setFocus()
            return
        if self.mode == "compact":
            self.set_mode("conversation")
        self.input.setFocus()
        self.input.selectAll()

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        if self.app.voice is not None:
            self.app.voice.interrupt()
        self.app.submit_input(text, "text")

    def toggle_mute(self) -> None:
        self.set_muted(self.mute_btn.isChecked())

    def set_muted(self, muted: bool) -> None:
        self.mute_btn.setChecked(muted)
        self.mute_btn.setText("🔇" if muted else "🎙")
        self.mute_action.setChecked(muted)
        if self.app.voice is not None:
            self.app.voice.set_muted(muted)
        self._refresh_status()

    def show_notice(self, text: str, ms: int = 8000) -> None:
        self.notice.setText(text)
        self.notice.setVisible(self.mode not in ("compact", "holo"))
        if self.mode == "holo":
            self.dashboard._event_line(text, theme.AMBER)
        self.status.setToolTip(text)
        QTimer.singleShot(ms, lambda: self.notice.setVisible(False) if self.notice.text() == text else None)
        if self.mode == "compact":
            self.status.setText(text[:120])

    def _set_state(self, state: str, detail: str = "") -> None:
        self.orb.set_state(state)
        label = theme.STATE_LABEL.get(state, state)
        self.state_label.setText(label)
        self.state_label.setToolTip(detail)
        if state == "completed":
            self._idle_timer.start(2500)
        else:
            self._idle_timer.stop()

    def _refresh_status(self) -> None:
        v = self.app.voice
        parts = []
        if v is None:
            parts.append("voice starting…" if not self.app.voice_error else "text only")
        else:
            s = v.status()
            mic = s["input"] or "no microphone"
            parts.append(("🔇 muted · " if s["muted"] else "") + ("🎧 " if s["input_is_headset"] else "⚠ ") + mic)
            if not s.get("listening", True):
                parts.insert(0, "💤 Not listening — say “start listening” or press Ctrl+Alt+M")
            elif s["wake_word"]:
                parts.append("“Hey JARVIS”")
            if self._speaker_label:
                parts.append(self._speaker_label)
        self.status.setText("  ·  ".join(parts))
        self.dashboard.set_status("  ·  ".join(parts))

    # ------------------------------------------------------------------ events
    def on_event(self, ev: Any) -> None:
        t, d = ev.topic, ev.data
        try:
            if t == "assistant.state":
                self._set_state(d["state"], d.get("detail", ""))
            elif t == "audio.level":
                lv = d["level"]
                speaking = d["source"] == "tts"
                if isinstance(self.orb, HologramWidget):
                    self.orb.set_level(lv, d["source"])
                elif speaking or self.orb.state in ("listening", "idle"):
                    self.orb.set_level(lv)
                self.wave.push(lv, theme.TEXT if speaking else theme.CYAN)
            elif t == "user.message":
                self.chat.add_user(d["text"], d.get("source", "text"))
            elif t == "assistant.delta":
                self.chat.stream(d["text"])
            elif t == "assistant.message":
                self.chat.finish(d["text"], d.get("model", ""))
            elif t == "assistant.task_started":
                self.task_strip.setText(f"▶ {d['goal'][:90]}")
            elif t == "agent.task":
                st = d["status"]
                if st in ("completed", "completed_unverified", "failed", "cancelled"):
                    mark = {"completed": "✓", "completed_unverified": "✓?", "failed": "✕", "cancelled": "■"}[st]
                    self.task_strip.setText(f"{mark} {d['goal'][:90]}")
                else:
                    self.task_strip.setText(f"▶ {d['goal'][:70]} · {st} · step {d['step']}")
            elif t == "agent.question":
                self._question_card(d)
            elif t == "permission.request":
                self._approval_card(d)
                self.show_window()
                if self.mode == "compact":
                    self.set_mode("conversation")
            elif t == "permission.resolved":
                card = self._cards.pop(d["id"], None)
                if card:
                    card.resolve("approved" if d["granted"] else ("timed out - denied" if d["timed_out"] else "denied"))
            elif t == "notify":
                self.tray.showMessage(d.get("title", "JARVIS"), d.get("message", ""), self.tray.icon(), 6000)
                self.chat.add_system(f"{d.get('title', '')}: {d.get('message', '')}")
            elif t == "notice":
                self.show_notice(d["text"])
            elif t in ("voice.ready", "voice.status"):
                self._refresh_status()
            elif t == "voice.listening":
                self.listen_action.setText(("Stop listening" if d["on"] else "Start listening") + "  (Ctrl+Alt+M)")
                self._refresh_status()
            elif t == "voice.error":
                self.show_notice(f"Voice problem ({d['where']}): {d['error'][:160]}")
                self._refresh_status()
            elif t == "voice.devices_changed":
                self.show_notice("Audio devices changed: " + ", ".join(
                    [f"+{a}" for a in d["added"]] + [f"-{r}" for r in d["removed"]])[:160])
            elif t == "voice.speaker":
                self._speaker_label = ("👤 " if d["accepted"] else "⛔ ") + d["label"]
                self._refresh_status()
            elif t == "voice.rejected":
                self.show_notice(f"Ignored a voice that isn't yours (match {d['score']:.2f}).", 4000)
            elif t == "voice.transcript" and not d["text"]:
                self.show_notice("Didn't catch that.", 2500)
            elif t == "model.fallback":
                self.show_notice(f"Model {d['candidate']} failed, switching: {d['error'][:100]}", 6000)
            elif t == "screen.alert":
                pass  # surfaced through notify
            elif t in ("jobs.changed", "todos.changed"):
                if self.tabs.currentWidget() is self.schedule and self.tabs.isVisible():
                    self.schedule.refresh()
            elif t == "memory.added" and self.tabs.currentWidget() is self.memory and self.tabs.isVisible():
                self.memory.refresh()
            self.dashboard.on_event(t, d)
            if t.startswith(("agent.", "tool.")):
                self.activity.on_event(t, d)
            if t.startswith("voice."):
                self.settings.on_event(t, d)
        except Exception:  # noqa: BLE001 - never let one bad event break the UI
            log.exception("UI failed handling %s", t)

    def _approval_card(self, d: dict[str, Any]) -> None:
        rid = d["id"]
        perms = self.app.permissions
        card = Card(f"APPROVAL NEEDED · {d['category'].upper()}", d["summary"], theme.AMBER, [
            ("Deny", lambda: perms.respond(rid, False), "Danger"),
            ("Allow for session", lambda: perms.respond(rid, True, remember=True), ""),
            ("Allow once", lambda: perms.respond(rid, True), "Primary"),
        ])
        self._cards[rid] = card
        if self.mode == "holo":
            self.dashboard.chat.add_card(card)
        else:
            self.chat.add_card(card)

    def _question_card(self, d: dict[str, Any]) -> None:
        card = Card("JARVIS NEEDS YOUR INPUT", d["question"] + "\n\nAnswer by voice or type your reply below.",
                    theme.CYAN, [])
        self.show_window()
        if self.mode == "holo":
            self.dashboard.chat.add_card(card)
            self.dashboard.input.setFocus()
            return
        self.chat.add_card(card)
        if self.mode == "compact":
            self.set_mode("conversation")
        self.input.setFocus()


def run(app: Jarvis, voice: bool = True) -> int:
    import sys

    qt = QApplication.instance() or QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)
    qt.setApplicationName("JARVIS")
    win = MainWindow(app)
    win.show()
    app.start(voice=voice)
    QTimer.singleShot(1200, win._refresh_status)
    code = qt.exec()
    app.shutdown()
    return code

