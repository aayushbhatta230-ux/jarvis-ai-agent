"""Short-term working memory for the file JARVIS most recently opened.

Whenever a file is read (through the Files view, a voice command, or a chat
request) its metadata and contents are parked here so that follow-up questions
like *"what is this file about?"*, *"suggest changes"*, or *"it"* / *"that file"*
are answered from the real content instead of the model guessing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

# How long an opened file stays "current" before JARVIS stops treating it as
# active context (set to 2 hours: long enough for a work session).
SESSION_TTL_SECONDS = 7200

_MAX_PROMPT_CHARS = 3000


class FileSession:
	"""Holds the most recently inspected file for conversational follow-ups."""

	def __init__(self) -> None:
		self._lock = threading.Lock()
		self.path: str | None = None
		self.name: str = ""
		self.ext: str = ""
		self.language: str = "text"
		self.lines: int = 0
		self.size_bytes: int = 0
		self.content: str = ""
		self.summary: str = ""
		self.purpose: str = ""
		self.opened_at: float = 0.0
		self.source: str = ""

	# ------------------------------------------------------------------ #
	def set(self, *, path: str, name: str, ext: str = "", language: str = "text",
	        lines: int = 0, size_bytes: int = 0, content: str = "",
	        summary: str = "", source: str = "read") -> None:
		"""Record the file JARVIS just read as the active working context."""
		with self._lock:
			self.path = str(path)
			self.name = name
			self.ext = (ext or Path(name).suffix or "").lower()
			self.language = language or "text"
			self.lines = int(lines or 0)
			self.size_bytes = int(size_bytes or 0)
			self.content = content or ""
			self.summary = summary or ""
			self.purpose = ""
			self.opened_at = time.time()
			self.source = source
		# Purpose is derived outside the lock (pure CPU, no I/O).
		try:
			from tools.file_analysis import describe_purpose
			self.purpose = describe_purpose(self.ext, self.content)
		except Exception:
			self.purpose = ""

	def clear(self) -> None:
		with self._lock:
			self.path = None
			self.name = ""
			self.content = ""
			self.summary = ""
			self.purpose = ""
			self.opened_at = 0.0

	def is_active(self) -> bool:
		with self._lock:
			return bool(self.path) and (time.time() - self.opened_at) < SESSION_TTL_SECONDS

	def snapshot(self) -> dict[str, Any]:
		with self._lock:
			if not self.path or (time.time() - self.opened_at) >= SESSION_TTL_SECONDS:
				return {}
			return {
				"path": self.path,
				"name": self.name,
				"ext": self.ext,
				"language": self.language,
				"lines": self.lines,
				"size_bytes": self.size_bytes,
				"content": self.content,
				"summary": self.summary,
				"purpose": self.purpose,
				"opened_at": self.opened_at,
				"source": self.source,
			}

	# ------------------------------------------------------------------ #
	def to_prompt_block(self, max_chars: int = _MAX_PROMPT_CHARS) -> str:
		"""Render the active file as a grounded block for the LLM prompt."""
		info = self.snapshot()
		if not info:
			return ""
		content = info["content"] or ""
		truncated = False
		if len(content) > max_chars:
			content = content[:max_chars]
			truncated = True
		lines_note = f"{info['lines']} lines"
		purpose = info.get("purpose") or info.get("summary") or ""
		return (
			f"=== FILE CURRENTLY OPEN ({info['name']}, {lines_note}) ===\n"
			f"Path: {info['path']}\n"
			f"Language: {info['language']}\n"
			f"What it does: {purpose}\n"
			+ ("CONTENT (truncated):\n" if truncated else "CONTENT:\n")
			+ content
		)

	def matches(self, query: str) -> bool:
		"""True when ``query`` refers to the active file (by name or stem)."""
		info = self.snapshot()
		if not info:
			return False
		q = (query or "").lower()
		if not q:
			return False
		name = info["name"].lower()
		stem = Path(name).stem.lower()
		return name in q or (len(stem) > 2 and stem in q)


_session: FileSession | None = None
_session_lock = threading.Lock()


def get_file_session() -> FileSession:
	global _session
	with _session_lock:
		if _session is None:
			_session = FileSession()
		return _session
