"""Permission policy, filesystem boundaries, approvals and audit trail.

Every tool declares a category. The configured policy maps each category to
``allow``, ``ask`` or ``deny``. Individual calls can be escalated (for
example a shell command that looks destructive is treated as
``destructive`` even though the tool is ``execute``). ``ask`` publishes a
``permission.request`` event and blocks the calling worker until the user
answers in the UI or by voice, or until the request times out (deny).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core.config import Config
from jarvis.core.db import Database
from jarvis.core.events import EventBus
from jarvis.core.logging_setup import redact

log = logging.getLogger(__name__)

CATEGORIES = ("read", "write", "execute", "system", "external", "destructive")
_SEVERITY = {c: i for i, c in enumerate(CATEGORIES)}

_DANGEROUS_COMMAND = re.compile(
    r"(?ix)"
    r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r|\bremove-item\b[^|;]*-recurse|\brmdir\s+/s|\brd\s+/s"
    r"|\bdel\s+/[sq]|\bformat(\.com)?\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bcipher\s+/w"
    r"|\breg(\.exe)?\s+delete|\bremove-itemproperty\b|\bset-executionpolicy\b"
    r"|\bshutdown(\.exe)?\b|\bstop-computer\b|\brestart-computer\b"
    r"|\bgit\s+(push\s+.*--force|push\s+-f\b|reset\s+--hard|clean\s+-[a-z]*f)"
    r"|\bnet\s+user\b|\bnetsh\s+advfirewall\b|\bset-mppreference\b|\bvssadmin\b"
    r"|\bclear-recyclebin\b|\bwevtutil\s+cl\b|\bdrop\s+(table|database)\b"
)
_SYSTEM_COMMAND = re.compile(
    r"(?i)\b(set-itemproperty|new-itemproperty|reg(\.exe)?\s+add|sc(\.exe)?\s+(config|stop|delete)"
    r"|set-service|stop-service|winget\s+(install|uninstall)|choco\s+(install|uninstall))\b"
)


class PermissionDenied(Exception):
    pass


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    category: str
    summary: str
    created: float = field(default_factory=time.time)
    answer: bool | None = None
    remember: bool = False
    done: threading.Event = field(default_factory=threading.Event)


def classify_command(command: str) -> str:
    """Return the effective category for a shell command string."""
    if _DANGEROUS_COMMAND.search(command):
        return "destructive"
    if _SYSTEM_COMMAND.search(command):
        return "system"
    return "execute"


class PermissionManager:
    def __init__(self, config: Config, events: EventBus, db: Database | None = None) -> None:
        self.config = config
        self.events = events
        self.db = db
        self._pending: dict[str, ApprovalRequest] = {}
        self._session_allow: set[str] = set()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- policy
    def policy(self, category: str) -> str:
        value = str(self.config.get(f"permissions.{category}", "ask")).lower()
        return value if value in ("allow", "ask", "deny") else "ask"

    def allowed_roots(self) -> list[Path]:
        roots = self.config.get("permissions.allowed_roots", ["~"]) or ["~"]
        return [Path(r).expanduser().resolve() for r in roots]

    def check_path(self, path: str | Path, write: bool = False) -> Path:
        """Resolve ``path`` and ensure it lies inside a configured root for writes.

        Reads are allowed anywhere the OS permits; writes are confined to the
        configured roots (default: the user's home folder).
        """
        resolved = Path(path).expanduser().resolve()
        if not write:
            return resolved
        for root in self.allowed_roots():
            if resolved == root or root in resolved.parents:
                return resolved
        raise PermissionDenied(
            f"Writing to {resolved} is outside the allowed folders "
            f"({', '.join(str(r) for r in self.allowed_roots())})."
        )

    # -------------------------------------------------------------- decision
    def authorize(self, tool: str, category: str, summary: str, task_id: str | None = None,
                  args: dict[str, Any] | None = None) -> None:
        """Raise :class:`PermissionDenied` unless the action may proceed."""
        decision = self.policy(category)
        key = f"{tool}:{category}"
        if decision == "ask" and key in self._session_allow:
            decision = "allow"
        if decision == "ask":
            granted = self.request_approval(tool, category, summary)
            decision = "allow" if granted else "deny"
            self.audit(tool, category, args, "approved" if granted else "refused", None, summary, task_id)
        if decision == "deny":
            if self.policy(category) == "deny":
                self.audit(tool, category, args, "denied-by-policy", None, summary, task_id)
            raise PermissionDenied(f"{tool} ({category}) was not permitted: {summary}")

    def request_approval(self, tool: str, category: str, summary: str) -> bool:
        req = ApprovalRequest(id=uuid.uuid4().hex[:10], tool=tool, category=category, summary=summary)
        with self._lock:
            self._pending[req.id] = req
        self.events.publish("permission.request", id=req.id, tool=tool, category=category,
                            summary=redact(summary))
        timeout = float(self.config.get("permissions.ask_timeout_seconds", 120))
        answered = req.done.wait(timeout)
        with self._lock:
            self._pending.pop(req.id, None)
        self.events.publish("permission.resolved", id=req.id, granted=bool(req.answer),
                            timed_out=not answered)
        if req.answer and req.remember:
            self._session_allow.add(f"{tool}:{category}")
        return bool(req.answer)

    def respond(self, request_id: str, granted: bool, remember: bool = False) -> bool:
        with self._lock:
            req = self._pending.get(request_id)
        if req is None:
            return False
        req.answer = granted
        req.remember = remember
        req.done.set()
        return True

    def respond_latest(self, granted: bool) -> bool:
        """Answer the most recent pending request (used for voice yes/no)."""
        with self._lock:
            if not self._pending:
                return False
            req = max(self._pending.values(), key=lambda r: r.created)
        return self.respond(req.id, granted)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": r.id, "tool": r.tool, "category": r.category, "summary": r.summary}
                for r in self._pending.values()
            ]

    # ----------------------------------------------------------------- audit
    def audit(self, tool: str, category: str, args: dict[str, Any] | None, decision: str,
              ok: bool | None, summary: str, task_id: str | None) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT INTO audit(ts, tool, category, args, decision, ok, summary, task_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), tool, category, redact(json.dumps(args or {}, default=str))[:4000],
                 decision, None if ok is None else int(ok), redact(summary)[:1000], task_id),
            )
        except Exception:  # noqa: BLE001 - auditing must never break a tool call
            log.exception("audit write failed")


def most_severe(*categories: str) -> str:
    return max(categories, key=lambda c: _SEVERITY.get(c, 0))
