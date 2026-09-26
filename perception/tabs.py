"""Tab, Menu, and UI element perception for JARVIS.

Enables natural visual awareness of open browser tabs, editor tabs, and application menus
(such as Chrome tabs, Antigravity IDE menus like File/Edit/Run, and editor tabs).
"""

from __future__ import annotations

import re
import sys
from typing import Any
import pyautogui

from perception.window import get_active_window, list_open_windows
from perception.vision import analyze_ui_elements


def get_tabs_and_menus() -> dict[str, Any]:
    """Analyze current screen and return active window, open tabs, and top menus."""
    win = get_active_window()
    app = win.app_name or ""
    title = win.title or ""

    elements = analyze_ui_elements()

    menus = []
    tabs = []
    top_actions = []

    # Known top menus for IDEs and desktop apps
    known_menus = {"file", "edit", "selection", "view", "go", "run", "terminal", "help", "window", "tools"}

    # Menu strip usually y in [10, 45], tabs usually y in [35, 95]
    for elem in elements:
        text = str(elem.get("text", "")).strip()
        if not text or len(text) < 2:
            continue
        lower = text.lower()
        left = elem.get("left", 0)
        top = elem.get("top", 0)
        width = elem.get("width", 0)
        height = elem.get("height", 0)

        # Top menu bar items
        if top < 45 and left < 800 and lower in known_menus:
            menus.append({
                "label": text,
                "center": (left + width // 2, top + height // 2),
                "bbox": (left, top, width, height),
            })
            continue

        # Open tabs row (either in browser or IDE editor tabs)
        if top < 110 and height < 50 and width > 15:
            # Filter out non-tab items
            if not any(k in lower for k in ("minimize", "maximize", "close")):
                tabs.append({
                    "label": text,
                    "center": (left + width // 2, top + height // 2),
                    "bbox": (left, top, width, height),
                })

    # If "edit" was found but "file" wasn't caught by OCR (often single letter 'F' or logo adjacent)
    has_file = any(m["label"].lower() == "file" for m in menus)
    edit_item = next((m for m in menus if m["label"].lower() == "edit"), None)
    if not has_file and edit_item:
        edit_x, edit_y = edit_item["center"]
        file_x = max(55, edit_x - 42)
        menus.insert(0, {
            "label": "File",
            "center": (file_x, edit_y),
            "bbox": (file_x - 15, edit_y - 10, 30, 20),
        })

    # Also list background open windows so user can switch between them
    open_apps = []
    for w in list_open_windows():
        if w.title and w.title != title:
            open_apps.append({
                "title": w.title,
                "app": w.app_name,
                "hwnd": w.hwnd,
            })

    return {
        "active_window": title,
        "active_app": app,
        "menus": menus,
        "tabs": tabs,
        "open_apps": open_apps,
    }


def find_and_click_target(target_label: str) -> dict[str, Any]:
    """Find a target (menu, tab, button, or conversation trigger) and click it.

    Supports natural targets like:
    - 'file', 'file menu', 'edit', 'run', 'terminal'
    - 'start a new convo', 'new conversation', 'new chat'
    - tab names like 'test_dom.js', 'mirzapur', 'github'
    """
    target = (target_label or "").strip()
    target_lower = target.lower()
    cleaned = re.sub(r"\b(the|a|an|menu|tab|button|icon|on)\b", "", target_lower).strip()

    win = get_active_window()
    app_lower = (win.app_name or "").lower()

    # Special handler: "start a new convo" / "new chat" in Antigravity or chat tools
    if any(k in target_lower for k in ("new convo", "new conversation", "new chat", "start a new")):
        # In Antigravity / VS Code, Ctrl+L or Ctrl+Shift+L opens a new conversation
        if "antigravity" in app_lower or "code" in app_lower:
            pyautogui.hotkey("ctrl", "shift", "l")
            return {
                "ok": True,
                "action": "shortcut",
                "message": "Started a new conversation in Antigravity.",
            }
        pyautogui.hotkey("ctrl", "n")
        return {
            "ok": True,
            "action": "shortcut",
            "message": "Opened a new conversation / file.",
        }

    # First check tabs and menus
    data = get_tabs_and_menus()

    # 1. Check menus (File, Edit, etc.)
    for menu in data.get("menus", []):
        m_label = menu["label"].lower()
        if cleaned == m_label or m_label in cleaned or cleaned in m_label:
            cx, cy = menu["center"]
            pyautogui.click(cx, cy)
            return {
                "ok": True,
                "action": "click_menu",
                "target": menu["label"],
                "coordinates": (cx, cy),
                "message": f"Clicked '{menu['label']}' menu at ({cx}, {cy}).",
            }

    # 2. Check open tabs
    for tab in data.get("tabs", []):
        t_label = tab["label"].lower()
        if cleaned == t_label or cleaned in t_label or t_label in cleaned:
            cx, cy = tab["center"]
            pyautogui.click(cx, cy)
            return {
                "ok": True,
                "action": "click_tab",
                "target": tab["label"],
                "coordinates": (cx, cy),
                "message": f"Clicked on tab '{tab['label']}' at ({cx}, {cy}).",
            }

    # 3. Check general screen OCR elements
    elements = analyze_ui_elements()
    best_elem = None
    best_score = 0.0

    for elem in elements:
        text = str(elem.get("text", "")).strip().lower()
        if not text:
            continue
        if text == cleaned:
            score = 1.0
        elif cleaned in text:
            score = 0.85
        elif text in cleaned:
            score = 0.7
        else:
            continue

        if score > best_score:
            best_score = score
            best_elem = elem

    if best_elem and best_score >= 0.7:
        left = best_elem.get("left", 0)
        top = best_elem.get("top", 0)
        w = best_elem.get("width", 10)
        h = best_elem.get("height", 10)
        cx = left + w // 2
        cy = top + h // 2
        pyautogui.click(cx, cy)
        return {
            "ok": True,
            "action": "click_element",
            "target": best_elem.get("text"),
            "coordinates": (cx, cy),
            "message": f"Clicked '{best_elem.get('text')}' at ({cx}, {cy}).",
        }

    # 4. Check if the target is another open window application
    for open_w in data.get("open_apps", []):
        w_title = open_w["title"].lower()
        w_app = open_w["app"].lower()
        if cleaned in w_title or cleaned in w_app:
            try:
                import win32gui
                hwnd = open_w["hwnd"]
                win32gui.SetForegroundWindow(hwnd)
                return {
                    "ok": True,
                    "action": "switch_window",
                    "target": open_w["title"],
                    "message": f"Switched to '{open_w['title']}' ({open_w['app']}).",
                }
            except Exception:
                pass

    return {
        "ok": False,
        "error": f"Could not find '{target_label}' on screen or in open tabs.",
        "message": f"I looked at the screen and couldn't find '{target_label}'.",
    }
