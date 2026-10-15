"""Cooperative cancellation and pause tokens for long-running work."""

from __future__ import annotations

import threading


class Cancelled(Exception):
    """Raised inside work that observed a cancellation request."""


class CancelToken:
    def __init__(self, parent: CancelToken | None = None) -> None:
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self.reason = ""
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set() or (self._parent is not None and self._parent.cancelled)

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self._cancel.set()
        self._resume.set()  # wake any paused waiter so it can observe cancellation

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def check(self) -> None:
        """Block while paused; raise :class:`Cancelled` if cancelled."""
        while not self._resume.wait(0.25):
            if self.cancelled:
                break
        if self.cancelled:
            raise Cancelled(self.reason or (self._parent.reason if self._parent else "cancelled"))

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; returns True if cancelled meanwhile."""
        return self._cancel.wait(seconds) or self.cancelled
