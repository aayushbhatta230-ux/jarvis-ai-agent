"""Colours, fonts and the Qt stylesheet for the JARVIS interface."""

from __future__ import annotations

from PySide6.QtGui import QColor

BG = "#04060A"
PANEL = "#070A0F"
SURFACE = "#0B1118"
SURFACE_2 = "#101923"
LINE = "#16222E"
TEXT = "#E6F4FF"
DIM = "#6F8396"
FAINT = "#3A4A5A"
CYAN = "#3FD8FF"
BLUE = "#2F7BFF"
GREEN = "#4CE29A"
AMBER = "#F5B342"
RED = "#FF6B4A"
FONT = "Segoe UI Variable Text, Segoe UI, sans-serif"
MONO = "Cascadia Mono, Consolas, monospace"

# Orb palette per assistant state: (core, glow, ring speed, pulse)
STATE_STYLE: dict[str, tuple[str, str, float, float]] = {
    "idle": ("#1F9FCB", "#0E3A52", 0.25, 0.25),
    "listening": ("#5FE6FF", "#1478A8", 0.6, 1.0),
    "processing": ("#6E8BFF", "#2A2F8F", 1.4, 0.7),
    "speaking": ("#E8FBFF", "#2AA7E0", 0.8, 1.0),
    "executing": ("#2F7BFF", "#123E9E", 2.2, 0.6),
    "analyzing_screen": ("#38F2E0", "#0E6D6A", 1.2, 0.6),
    "coding": ("#3CF0B0", "#0F6B52", 1.6, 0.6),
    "waiting": ("#F5B342", "#6B4A12", 0.4, 0.4),
    "recovering": ("#FF6B4A", "#7A2416", 1.0, 0.8),
    "completed": ("#4CE29A", "#155E3D", 0.6, 0.5),
    "paused": ("#7E8C99", "#2A333C", 0.1, 0.1),
}

STATE_LABEL = {
    "idle": "Standing by", "listening": "Listening", "processing": "Thinking", "speaking": "Speaking",
    "executing": "Working", "analyzing_screen": "Looking at your screen", "coding": "Coding",
    "waiting": "Waiting", "recovering": "Recovering", "completed": "Done", "paused": "Paused",
}


def qcolor(hex_or_rgba: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_or_rgba)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


STYLESHEET = f"""
* {{ font-family: {FONT}; color: {TEXT}; }}
QWidget#Root {{ background: transparent; }}
QFrame#Shell {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 18px; }}
/* Scroll areas (e.g. Settings) otherwise get Windows' light-grey viewport, which hides our light text. */
QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel#Status {{ color: {DIM}; font-size: 11px; letter-spacing: 0.5px; }}
QLabel#StateLabel {{ color: {TEXT}; font-size: 13px; font-weight: 600; letter-spacing: 1px; }}
QLabel#Title {{ color: {CYAN}; font-size: 12px; font-weight: 700; letter-spacing: 4px; }}
QLabel#Section {{ color: {DIM}; font-size: 10px; font-weight: 700; letter-spacing: 2px; }}
QLabel#Muted {{ color: {DIM}; font-size: 11px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; padding: 7px 10px;
    selection-background-color: {BLUE}; font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 1px solid {CYAN}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE}; selection-background-color: {BLUE}; }}
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px 12px; font-size: 12px;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton#Primary {{ background: #0E3A52; border-color: {CYAN}; }}
QPushButton#Danger {{ border-color: #5A2418; color: {RED}; }}
QPushButton#Icon {{ background: transparent; border: none; color: {DIM}; font-size: 15px; padding: 4px 6px; }}
QPushButton#Icon:hover {{ color: {CYAN}; }}
QPushButton#Icon:checked {{ color: {CYAN}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {DIM}; padding: 8px 12px; border: none; font-size: 12px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {CYAN}; }}
QListWidget, QTreeWidget, QTableWidget {{
    background: transparent; border: none; font-size: 12px; outline: none;
}}
QListWidget::item, QTreeWidget::item {{ padding: 5px 4px; border-bottom: 1px solid {LINE}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {SURFACE_2}; }}
QHeaderView::section {{ background: transparent; color: {DIM}; border: none; border-bottom: 1px solid {LINE};
    padding: 4px; font-size: 11px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {FAINT}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QCheckBox {{ font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {FAINT}; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #0E3A52; }}
QProgressBar {{ background: {SURFACE}; border: none; border-radius: 3px; height: 6px; text-align: center; font-size: 0px; }}
QProgressBar::chunk {{ background: {CYAN}; border-radius: 3px; }}
"""
