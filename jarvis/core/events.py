"""Thread-safe publish/subscribe event bus.

Every subsystem reports state through structured events (``topic`` +
``data`` dict). The UI subscribes and marshals events onto the Qt thread;
tests subscribe directly. Handlers run synchronously on the publisher's
thread, so they must be quick and must not block.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# Assistant visual / behavioural states shown by the orb.
class State:
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    SCREEN = "analyzing_screen"
    CODING = "coding"
    WAITING = "waiting"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    PAUSED = "paused"

    ALL = (IDLE, LISTENING, PROCESSING, SPEAKING, EXECUTING, SCREEN, CODING, WAITING,
           RECOVERING, COMPLETED, PAUSED)


@dataclass
class Event:
    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self._lock = threading.RLock()
        self.recent: deque[Event] = deque(maxlen=history)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic (``*`` for everything). Returns an unsubscribe function."""
        with self._lock:
            self._subs.setdefault(topic, []).append(handler)

        def _unsub() -> None:
            with self._lock:
                handlers = self._subs.get(topic, [])
                if handler in handlers:
                    handlers.remove(handler)

        return _unsub

    def publish(self, topic: str, **data: Any) -> Event:
        event = Event(topic, data)
        self.recent.append(event)
        with self._lock:
            handlers = list(self._subs.get(topic, ())) + list(self._subs.get("*", ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break publishing
                log.exception("event handler failed for topic %s", topic)
        return event

    def state(self, state: str, detail: str = "") -> None:
        self.publish("assistant.state", state=state, detail=detail)
