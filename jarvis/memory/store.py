"""Persistent memory: long-term facts, project memory, conversation log.

Retrieval is hybrid: SQLite FTS5 keyword ranking plus embedding cosine
similarity (when an embedding model is available), blended with importance
and recency. Only the most relevant memories are injected into a prompt.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from jarvis.core import paths

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)

KINDS = ("preference", "fact", "project", "decision", "routine", "fix", "goal", "contact", "note", "task_outcome")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip(" .")


class MemoryStore:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.db = app.db
        self._embed_ok: bool | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ embeddings
    def _embed(self, texts: list[str]) -> tuple[str, list[np.ndarray]] | None:
        if self._embed_ok is False:
            return None
        try:
            model, vecs = self.app.router.embed(texts)
            self._embed_ok = True
            return model, [np.asarray(v, dtype=np.float32) for v in vecs]
        except Exception as exc:  # noqa: BLE001
            if self._embed_ok is None:
                log.warning("embeddings unavailable; memory uses keyword search only (%s)", exc)
            self._embed_ok = False
            return None

    def retry_embeddings(self) -> int:
        """Embed memories stored while no embedding model was available."""
        self._embed_ok = None
        rows = self.db.query("SELECT id, content FROM memories WHERE embedding IS NULL LIMIT 500")
        done = 0
        for i in range(0, len(rows), 32):
            batch = rows[i : i + 32]
            res = self._embed([r["content"] for r in batch])
            if not res:
                break
            model, vecs = res
            for r, v in zip(batch, vecs, strict=False):
                self.db.execute("UPDATE memories SET embedding=?, embed_model=? WHERE id=?", (v.tobytes(), model, r["id"]))
                done += 1
        return done

    # ----------------------------------------------------------------- write
    def add(self, content: str, kind: str = "fact", project: str = "", tags: str = "", importance: float = 0.5,
            source: str = "user") -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("empty memory")
        kind = kind if kind in KINDS else "fact"
        now = time.time()
        with self._lock:
            # Exact/near duplicate -> refresh instead of adding
            for row in self.db.query("SELECT id, content FROM memories WHERE kind=? AND project=?", (kind, project)):
                if _norm(row["content"]) == _norm(content):
                    self.db.execute("UPDATE memories SET updated=?, importance=MAX(importance, ?) WHERE id=?",
                                    (now, importance, row["id"]))
                    return {"id": row["id"], "status": "refreshed"}
            emb = self._embed([content])
            if emb:
                similar = self._vector_search(emb[1][0], 1, kind=kind)
                if similar and similar[0][1] > 0.93:
                    mid = similar[0][0]
                    self.db.execute("UPDATE memories SET content=?, updated=?, importance=MAX(importance, ?), "
                                    "embedding=? WHERE id=?", (content, now, importance, emb[1][0].tobytes(), mid))
                    return {"id": mid, "status": "updated similar memory"}
            cur = self.db.execute(
                "INSERT INTO memories(kind, content, project, tags, importance, source, created, updated, embedding, "
                "embed_model) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, content, project, tags, float(importance), source, now, now,
                 emb[1][0].tobytes() if emb else None, emb[0] if emb else None),
            )
        self.app.events.publish("memory.added", id=cur.lastrowid, kind=kind, content=content[:200])
        return {"id": cur.lastrowid, "status": "stored"}

    def update(self, mid: int, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("content", "kind", "project", "tags", "importance")}
        if not allowed:
            return
        sets = ", ".join(f"{k}=?" for k in allowed)
        params = list(allowed.values())
        if "content" in allowed:
            emb = self._embed([allowed["content"]])
            sets += ", embedding=?"
            params.append(emb[1][0].tobytes() if emb else None)
        self.db.execute(f"UPDATE memories SET {sets}, updated=? WHERE id=?", (*params, time.time(), mid))

    def delete(self, mid: int) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0

    # ------------------------------------------------------------------ read
    def _vector_search(self, q: np.ndarray, k: int, kind: str | None = None) -> list[tuple[int, float]]:
        sql = "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
        params: tuple[Any, ...] = ()
        if kind:
            sql += " AND kind=?"
            params = (kind,)
        rows = self.db.query(sql, params)
        if not rows:
            return []
        dim = q.shape[0]
        rows = [r for r in rows if len(r["embedding"]) == dim * 4]
        if not rows:
            return []
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        qn = q / (np.linalg.norm(q) + 1e-9)
        scores = mat @ qn
        idx = np.argsort(-scores)[:k]
        return [(rows[int(i)]["id"], float(scores[i])) for i in idx]

    def _keyword_search(self, query: str, k: int) -> list[tuple[int, float]]:
        terms = [t for t in re.findall(r"[\w']+", query.lower()) if len(t) > 2]
        if not terms:
            return []
        if self.db.fts:
            fts_q = " OR ".join(f'"{t}"' for t in terms[:12])
            try:
                rows = self.db.query("SELECT rowid AS id, bm25(memories_fts) AS score FROM memories_fts "
                                     "WHERE memories_fts MATCH ? ORDER BY score LIMIT ?", (fts_q, k))
                # bm25: lower is better (negative); map to 0..1
                return [(r["id"], 1.0 / (1.0 + max(0.0, r["score"] + 10) / 10)) for r in rows]
            except Exception:  # noqa: BLE001
                log.debug("fts query failed", exc_info=True)
        like = " OR ".join("content LIKE ?" for _ in terms[:8])
        rows = self.db.query(f"SELECT id FROM memories WHERE {like} LIMIT ?", (*[f"%{t}%" for t in terms[:8]], k))
        return [(r["id"], 0.5) for r in rows]

    def search(self, query: str, k: int = 8, kinds: list[str] | None = None, project: str = "",
               min_score: float = 0.25) -> list[dict[str, Any]]:
        scores: dict[int, float] = {}
        for mid, s in self._keyword_search(query, 30):
            scores[mid] = scores.get(mid, 0) + 0.45 * s
        emb = self._embed([query])
        if emb:
            for mid, s in self._vector_search(emb[1][0], 30):
                scores[mid] = scores.get(mid, 0) + 0.75 * max(0.0, s)
        if not scores:
            return []
        rows = {r["id"]: r for r in self.db.query(
            f"SELECT id, kind, content, project, tags, importance, updated, use_count FROM memories WHERE id IN "
            f"({','.join('?' * len(scores))})", tuple(scores))}
        ranked = []
        now = time.time()
        for mid, s in scores.items():
            r = rows.get(mid)
            if not r:
                continue
            if kinds and r["kind"] not in kinds:
                continue
            age_days = (now - r["updated"]) / 86400
            score = s + 0.15 * r["importance"] + 0.05 * np.exp(-age_days / 30)
            if project and r["project"] == project:
                score += 0.1
            if score >= min_score:
                ranked.append({**r, "score": round(float(score), 3)})
        ranked.sort(key=lambda x: -x["score"])
        top = ranked[:k]
        if top:
            self.db.execute(f"UPDATE memories SET last_used=?, use_count=use_count+1 WHERE id IN "
                            f"({','.join('?' * len(top))})", (now, *[r["id"] for r in top]))
        return top

    def core_profile(self) -> list[dict[str, Any]]:
        """High-importance preferences always included (kept small)."""
        return self.db.query("SELECT id, kind, content FROM memories WHERE kind IN ('preference','goal','routine') "
                             "AND importance >= 0.7 ORDER BY importance DESC, updated DESC LIMIT 8")

    def context_block(self, query: str, project: str = "") -> str:
        seen: set[int] = set()
        lines = []
        for r in self.core_profile():
            seen.add(r["id"])
            lines.append(f"- [{r['kind']}] {r['content']}")
        for r in self.search(query, k=7, project=project):
            if r["id"] in seen:
                continue
            lines.append(f"- [{r['kind']}{'/' + Path(r['project']).name if r['project'] else ''}] {r['content']}")
        return "\n".join(lines)

    def list(self, kind: str = "", query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, content, project, tags, importance, created, updated, use_count FROM memories"
        conds, params = [], []
        if kind:
            conds.append("kind=?")
            params.append(kind)
        if query:
            conds.append("content LIKE ?")
            params.append(f"%{query}%")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY updated DESC LIMIT ?"
        return self.db.query(sql, (*params, limit))

    # ---------------------------------------------------------- projects
    def note_project(self, root: Path) -> None:
        self.add(f"Active project: {root}", kind="project", project=str(root), importance=0.6, source="auto")
        self.db.kv_set("last_project", str(root))

    # -------------------------------------------------------- conversation
    def add_message(self, session: str, role: str, content: str, meta: dict[str, Any] | None = None) -> None:
        self.db.execute("INSERT INTO messages(session, role, content, ts, meta) VALUES (?,?,?,?,?)",
                        (session, role, content, time.time(), json.dumps(meta or {})))

    def recent_messages(self, session: str, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT role, content, ts FROM messages WHERE session=? ORDER BY id DESC LIMIT ?",
                             (session, limit))
        return list(reversed(rows))

    def last_session(self) -> str | None:
        row = self.db.one("SELECT session FROM messages WHERE session != 'legacy' ORDER BY id DESC LIMIT 1")
        return row["session"] if row else None

    # ------------------------------------------------------- export/import
    def export(self, dest: Path | None = None) -> Path:
        dest = dest or paths.backups_dir() / f"memories-{time.strftime('%Y%m%d-%H%M%S')}.json"
        rows = self.db.query("SELECT kind, content, project, tags, importance, source, created, updated FROM memories")
        dest.write_text(json.dumps({"version": 1, "memories": rows}, indent=2), encoding="utf-8")
        return dest

    def import_file(self, src: Path) -> int:
        data = json.loads(src.read_text(encoding="utf-8"))
        count = 0
        for m in data.get("memories", []):
            self.add(m["content"], m.get("kind", "fact"), m.get("project", ""), m.get("tags", ""),
                     float(m.get("importance", 0.5)), m.get("source", "import"))
            count += 1
        return count

    def import_legacy(self, project_root: Path) -> int:
        """One-time import of the previous JARVIS JSON memory files."""
        if self.db.kv_get("legacy_imported"):
            return 0
        count = 0
        contacts = project_root / "memory" / "contacts.json"
        if contacts.exists():
            try:
                for name, email in json.loads(contacts.read_text(encoding="utf-8")).items():
                    if "example.com" in email:
                        continue
                    self.add(f"Contact '{name}': {email}", kind="contact", importance=0.6, source="legacy")
                    count += 1
            except (OSError, json.JSONDecodeError, AttributeError):
                log.warning("could not import legacy contacts")
        notes = project_root / "database" / "notes.json"
        if notes.exists():
            try:
                for n in json.loads(notes.read_text(encoding="utf-8")):
                    self.add(f"{n.get('title', 'Note')}: {n.get('content', '')}", kind="note", source="legacy")
                    count += 1
            except (OSError, json.JSONDecodeError, AttributeError):
                log.warning("could not import legacy notes")
        history = project_root / "memory" / "history.json"
        if history.exists():
            try:
                for h in json.loads(history.read_text(encoding="utf-8"))[-200:]:
                    role = "assistant" if h.get("role") == "assistant" else "user"
                    self.add_message("legacy", role, h.get("text", ""), {"ts": h.get("ts")})
            except (OSError, json.JSONDecodeError, AttributeError):
                log.warning("could not import legacy history")
        self.db.kv_set("legacy_imported", True)
        return count
