"""Smart Timers, Alarms, and Reminder Scheduler for JARVIS.

Manages background countdown timers, notifications, and spoken alerts.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TimerItem:
    def __init__(self, timer_id: int, duration_seconds: float, label: str, callback: Callable[[TimerItem], None] | None = None) -> None:
        self.timer_id = timer_id
        self.duration_seconds = duration_seconds
        self.label = label
        self.start_time = time.time()
        self.end_time = self.start_time + duration_seconds
        self.callback = callback
        self.cancelled = False
        self.completed = False

    @property
    def remaining_seconds(self) -> float:
        if self.cancelled or self.completed:
            return 0.0
        return max(0.0, self.end_time - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.timer_id,
            "label": self.label,
            "duration_seconds": self.duration_seconds,
            "remaining_seconds": round(self.remaining_seconds, 1),
            "completed": self.completed,
            "cancelled": self.cancelled,
        }


class SmartAlarmManager:
    """Thread-safe timer and reminder manager."""

    def __init__(self) -> None:
        self._timers: dict[int, TimerItem] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._on_timer_done_hooks: list[Callable[[TimerItem], None]] = []

    def register_done_hook(self, hook: Callable[[TimerItem], None]) -> None:
        self._on_timer_done_hooks.append(hook)

    def set_timer(self, minutes: float, label: str = "Timer") -> TimerItem:
        """Set a countdown timer in minutes."""
        duration_seconds = max(0.1, float(minutes) * 60.0)
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            item = TimerItem(tid, duration_seconds, label.strip() or "Timer")
            self._timers[tid] = item

        # Start countdown thread
        def _runner():
            time.sleep(duration_seconds)
            with self._lock:
                if item.cancelled:
                    return
                item.completed = True

            # Trigger hooks
            for hook in self._on_timer_done_hooks:
                try:
                    hook(item)
                except Exception as exc:
                    print(f"[ALARM] Hook error: {exc}")

        t = threading.Thread(target=_runner, name=f"jarvis-timer-{tid}", daemon=True)
        t.start()
        return item

    def list_timers(self) -> list[dict[str, Any]]:
        with self._lock:
            active = [t.to_dict() for t in self._timers.values() if not t.completed and not t.cancelled]
        return sorted(active, key=lambda x: x["remaining_seconds"])

    def cancel_timer(self, timer_id: int) -> bool:
        with self._lock:
            if timer_id in self._timers:
                self._timers[timer_id].cancelled = True
                return True
        return False

    def cancel_all(self) -> int:
        count = 0
        with self._lock:
            for t in self._timers.values():
                if not t.completed and not t.cancelled:
                    t.cancelled = True
                    count += 1
        return count


# Global singleton instance
_alarm_manager: SmartAlarmManager | None = None


def get_alarm_manager() -> SmartAlarmManager:
    global _alarm_manager
    if _alarm_manager is None:
        _alarm_manager = SmartAlarmManager()
    return _alarm_manager
