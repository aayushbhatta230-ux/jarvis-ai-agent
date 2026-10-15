"""JARVIS entry point.

    python -m jarvis              launch the desktop assistant
    python -m jarvis --selftest   print the diagnostics report and exit
    python -m jarvis --cli        text-only console mode (no GUI, no voice)
    python -m jarvis --no-voice   desktop UI without the voice pipeline
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys


def _single_instance() -> object | None:
    """Return a mutex handle, or None if another JARVIS is already running."""
    if sys.platform != "win32":
        return object()
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JARVIS-desktop-assistant")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS desktop AI assistant")
    parser.add_argument("--selftest", action="store_true", help="run diagnostics and exit")
    parser.add_argument("--cli", action="store_true", help="text-only console mode")
    parser.add_argument("--no-voice", action="store_true", help="start without the voice pipeline")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)

    from jarvis.core import logging_setup

    logging_setup.setup(logging.DEBUG if args.debug else logging.INFO)
    log = logging.getLogger("jarvis")

    from jarvis.app import Jarvis

    if args.selftest:
        app = Jarvis()
        app.scheduler.start()
        report = app.diagnostics.run()
        print(app.diagnostics.format(report))
        app.shutdown()
        return 0 if not any(c.status == "unavailable" for c in report) else 1

    if args.cli:
        return _cli(Jarvis())

    mutex = _single_instance()
    if mutex is None:
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtNetwork import QLocalSocket

            _qt_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
            sock = QLocalSocket()
            sock.connectToServer("jarvis-single-instance")
            if sock.waitForConnected(1500):
                sock.write(b"summon")
                sock.waitForBytesWritten(1000)
                sock.disconnectFromServer()
        except Exception:
            pass
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "JARVIS")
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        log.info("JARVIS is already running; summoned window.")
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001 - already set by Qt or unsupported
        pass
    from jarvis.ui.main_window import run

    return run(Jarvis(), voice=not args.no_voice)


def _cli(app) -> int:  # type: ignore[no-untyped-def]
    import threading

    done = threading.Event()

    def show(ev) -> None:  # type: ignore[no-untyped-def]
        t, d = ev.topic, ev.data
        if t == "assistant.delta":
            print(d["text"], end="", flush=True)
        elif t == "assistant.message":
            print("\nJARVIS:", d["text"], flush=True)
            done.set()
        elif t == "agent.step" and d["kind"] == "tool":
            print(f"  [{d['status']}] {d['tool']} {d['args'][:100]}", flush=True)
        elif t == "permission.request":
            ans = input(f"\nAllow {d['summary']}? [y/N] ").strip().lower()
            app.permissions.respond(d["id"], ans in ("y", "yes"))
        elif t == "notify":
            print(f"\n[{d['title']}] {d['message']}", flush=True)

    app.events.subscribe("*", show)
    app.start(voice=False)
    print("JARVIS console mode. Type 'quit' to exit.")
    try:
        while True:
            text = input("\nyou> ").strip()
            if text.lower() in ("quit", "exit"):
                break
            if text:
                done.clear()
                app.submit_input(text, "text")
                done.wait(900)
    except (EOFError, KeyboardInterrupt):
        pass
    app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
