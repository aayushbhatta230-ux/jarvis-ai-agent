"""Windows Clipboard Intelligence for JARVIS.

Enables reading, writing, inspecting, and summarizing the system clipboard.
"""

from __future__ import annotations

import subprocess

from tools.win_exec import run_no_window
from typing import Any


def get_clipboard_text() -> str | None:
    """Read text content from Windows clipboard."""
    try:
        proc = run_no_window(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception as exc:
        print(f"[CLIPBOARD] Read error: {exc}")
    return None


def set_clipboard_text(text: str) -> bool:
    """Write text content to Windows clipboard."""
    try:
        proc = run_no_window(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "$input | Set-Clipboard"],
            input=text,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception as exc:
        print(f"[CLIPBOARD] Write error: {exc}")
    return False


def inspect_clipboard() -> dict[str, Any]:
    """Retrieve and format current clipboard content."""
    content = get_clipboard_text()
    if not content:
        return {
            "has_content": False,
            "spoken": "Your clipboard is currently empty, sir.",
            "display": "📋 **Clipboard**: Empty",
        }

    preview = content[:200].replace("\n", " ")
    word_count = len(content.split())
    char_count = len(content)

    return {
        "has_content": True,
        "content": content,
        "length": char_count,
        "words": word_count,
        "spoken": f"Your clipboard contains {word_count} words: \"{preview[:100]}...\"",
        "display": f"📋 **Clipboard** ({word_count} words, {char_count} chars):\n```\n{content[:500]}\n```" + ("\n*(truncated)*" if len(content) > 500 else ""),
    }
