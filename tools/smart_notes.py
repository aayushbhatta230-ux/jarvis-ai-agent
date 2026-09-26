"""Smart Notes Engine integrated with Neural Vector Memory.

Allows taking, listing, searching, and deleting notes with full semantic RAG.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.neural_memory import get_neural_memory


class SmartNotesManager:
    """Persistent notes manager with semantic vector indexing."""

    def __init__(self, file_path: str | Path | None = None) -> None:
        self.file_path = Path(file_path) if file_path else Path(__file__).resolve().parent.parent / "database" / "notes.json"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = get_neural_memory()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.file_path.is_file():
            self.file_path.write_text("[]", encoding="utf-8")

    def _read_all(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write_all(self, notes: list[dict[str, Any]]) -> None:
        self.file_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")

    def add_note(self, content: str, title: str | None = None) -> dict[str, Any]:
        """Save a new note and index it into neural memory."""
        clean = content.strip()
        if not clean:
            return {}

        now = time.time()
        note_id = int(now * 1000) % 1000000
        first_line = clean.split("\n")[0][:40]
        note_title = title.strip() if title else (first_line if len(clean) > 40 else "Note")

        note = {
            "id": note_id,
            "title": note_title,
            "content": clean,
            "timestamp": now,
        }

        notes = self._read_all()
        notes.append(note)
        self._write_all(notes)

        # Index into neural memory for semantic search
        self.memory.store(
            f"Note: {note_title} - {clean}",
            category="note",
            metadata={"note_id": note_id, "title": note_title},
            importance=1.1,
        )

        return note

    def list_notes(self, limit: int = 5) -> list[dict[str, Any]]:
        """List the most recent notes."""
        notes = self._read_all()
        notes.sort(key=lambda n: n.get("timestamp", 0), reverse=True)
        return notes[:limit]

    def search_notes(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Search notes semantically using neural vector memory."""
        results = self.memory.search(query, category="note", top_k=limit, min_score=0.08)
        if not results:
            # Fallback to keyword search
            q_lower = query.lower()
            all_notes = self._read_all()
            matched = [n for n in all_notes if q_lower in n.get("content", "").lower() or q_lower in n.get("title", "").lower()]
            return matched[:limit]

        all_notes = {n.get("id"): n for n in self._read_all()}
        found: list[dict[str, Any]] = []
        for r in results:
            nid = r.get("metadata", {}).get("note_id")
            if nid and nid in all_notes:
                item = dict(all_notes[nid])
                item["score"] = r.get("score")
                found.append(item)

        return found

    def delete_note(self, note_id: int) -> bool:
        """Delete a note by ID."""
        notes = self._read_all()
        initial_len = len(notes)
        notes = [n for n in notes if n.get("id") != note_id]
        if len(notes) < initial_len:
            self._write_all(notes)
            return True
        return False


_notes_manager: SmartNotesManager | None = None


def get_notes_manager() -> SmartNotesManager:
    global _notes_manager
    if _notes_manager is None:
        _notes_manager = SmartNotesManager()
    return _notes_manager
