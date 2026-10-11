"""Conversation view: minimal message stream with live streaming and inline cards."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from jarvis.ui import theme


class Message(QFrame):
    def __init__(self, role: str, text: str, meta: str = "") -> None:
        super().__init__()
        self.role = role
        self.raw = text
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse |
                                          Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.body.setOpenExternalLinks(True)
        self.body.setTextFormat(Qt.TextFormat.MarkdownText)
        if role == "user":
            self.body.setStyleSheet(f"color: {theme.DIM}; font-size: 13px;")
            self.body.setAlignment(Qt.AlignmentFlag.AlignRight)
        elif role == "system":
            self.body.setStyleSheet(f"color: {theme.FAINT}; font-size: 11px; font-style: italic;")
        else:
            self.body.setStyleSheet(f"color: {theme.TEXT}; font-size: 14px; line-height: 140%;")
        lay.addWidget(self.body)
        self.meta = QLabel(meta)
        self.meta.setStyleSheet(f"color: {theme.FAINT}; font-size: 10px;")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight if role == "user" else Qt.AlignmentFlag.AlignLeft)
        self.meta.setVisible(bool(meta))
        lay.addWidget(self.meta)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.raw = text
        self.body.setText(text)

    def append(self, piece: str) -> None:
        self.set_text(self.raw + piece)


class Card(QFrame):
    """Inline card for approvals and questions."""

    def __init__(self, title: str, body: str, color: str, buttons: list[tuple[str, Callable[[], None], str]]) -> None:
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: {theme.SURFACE}; border: 1px solid {color}; border-radius: 12px; }}"
                           f"QLabel {{ border: none; background: transparent; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        t = QLabel(title)
        t.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        lay.addWidget(t)
        b = QLabel(body)
        b.setWordWrap(True)
        b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b.setStyleSheet("font-size: 12px;")
        lay.addWidget(b)
        row = QHBoxLayout()
        row.addStretch(1)
        self.buttons = []
        for label, cb, obj in buttons:
            btn = QPushButton(label)
            btn.setObjectName(obj)
            btn.clicked.connect(cb)
            row.addWidget(btn)
            self.buttons.append(btn)
        lay.addLayout(row)

    def resolve(self, note: str) -> None:
        for b in self.buttons:
            b.setEnabled(False)
            b.setVisible(False)
        lbl = QLabel(note)
        lbl.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        self.layout().addWidget(lbl)


class ConversationView(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; } QWidget#Feed { background: transparent; }")
        self.feed = QWidget()
        self.feed.setObjectName("Feed")
        self.lay = QVBoxLayout(self.feed)
        self.lay.setContentsMargins(4, 4, 10, 4)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)
        self.setWidget(self.feed)
        self._streaming: Message | None = None
        self.verticalScrollBar().rangeChanged.connect(self._autoscroll)
        self._stick = True
        self.verticalScrollBar().valueChanged.connect(
            lambda v: setattr(self, "_stick", v >= self.verticalScrollBar().maximum() - 40))

    def _autoscroll(self, _min: int, maximum: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(maximum)

    def _add(self, w: QWidget) -> None:
        self.lay.insertWidget(self.lay.count() - 1, w)
        # keep memory bounded
        while self.lay.count() > 201:
            item = self.lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def add_user(self, text: str, source: str = "text") -> None:
        self._streaming = None
        self._add(Message("user", text, ("🎙 " if source == "voice" else "") + time.strftime("%H:%M")))

    def add_system(self, text: str) -> None:
        self._add(Message("system", text))

    def stream(self, piece: str) -> None:
        if self._streaming is None:
            self._streaming = Message("assistant", "")
            self._add(self._streaming)
        self._streaming.append(piece)

    def finish(self, text: str, meta: str = "") -> None:
        if self._streaming is not None:
            self._streaming.set_text(text)
            if meta:
                self._streaming.meta.setText(meta)
                self._streaming.meta.setVisible(True)
            self._streaming = None
        else:
            self._add(Message("assistant", text, meta))

    def add_card(self, card: Card) -> None:
        self._streaming = None
        self._add(card)
