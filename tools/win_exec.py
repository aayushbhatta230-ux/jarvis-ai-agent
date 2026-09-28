"""Shared Windows subprocess helpers.

Every helper process JARVIS spawns must be created with ``CREATE_NO_WINDOW``.
Without it, each one briefly flashes a black console window on the desktop,
which looks to the user like the terminal is glitching or lagging.
"""

from __future__ import annotations

import os
import subprocess

#: Pass as ``creationflags=`` on Windows to suppress the console window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def popen_no_window(*args, **kwargs):
    """``subprocess.Popen`` that never flashes a console window."""
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(*args, **kwargs)


def run_no_window(*args, **kwargs):
    """``subprocess.run`` that never flashes a console window."""
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(*args, **kwargs)
