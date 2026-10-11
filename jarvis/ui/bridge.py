"""Marshal event-bus events (from worker threads) onto the Qt GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from jarvis.core.events import Event, EventBus


class EventBridge(QObject):
    event = Signal(object)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._unsub = bus.subscribe("*", self._forward)

    def _forward(self, ev: Event) -> None:
        # Signal emission from a non-GUI thread is queued to the receiver's thread.
        self.event.emit(ev)

    def close(self) -> None:
        self._unsub()
