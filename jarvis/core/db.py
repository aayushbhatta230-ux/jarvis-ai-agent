"""SQLite persistence with versioned migrations, backups and recovery."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jarvis.core import paths

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1 - initial schema
    """
    CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL DEFAULT 'fact',
        content TEXT NOT NULL,
        project TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        importance REAL NOT NULL DEFAULT 0.5,
        source TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        updated REAL NOT NULL,
        last_used REAL,
        use_count INTEGER NOT NULL DEFAULT 0,
        embedding BLOB,
        embed_model TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_memories_kind ON memories(kind);
    CREATE INDEX IF NOT EXISTS ix_memories_project ON memories(project);

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts REAL NOT NULL,
        meta TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session, id);

    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL,
        plan TEXT,
        result TEXT,
        error TEXT,
        model TEXT,
        checkpoint TEXT,
        project TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user'
    );
    CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);

    CREATE TABLE IF NOT EXISTS task_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        tool TEXT,
        args TEXT,
        result TEXT,
        status TEXT NOT NULL,
        ts REAL NOT NULL,
        duration REAL
    );
    CREATE INDEX IF NOT EXISTS ix_steps_task ON task_steps(task_id, id);

    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        tool TEXT NOT NULL,
        category TEXT NOT NULL,
        args TEXT,
        decision TEXT NOT NULL,
        ok INTEGER,
        summary TEXT,
        task_id TEXT
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        schedule TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        next_run REAL,
        last_run REAL,
        last_status TEXT,
        created REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        started REAL NOT NULL,
        finished REAL,
        status TEXT NOT NULL,
        output TEXT
    );

    CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        due REAL,
        priority INTEGER NOT NULL DEFAULT 2,
        status TEXT NOT NULL DEFAULT 'open',
        project TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        completed REAL
    );

    CREATE TABLE IF NOT EXISTS voiceprints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        embedding BLOB NOT NULL,
        samples INTEGER NOT NULL,
        model TEXT NOT NULL,
        created REAL NOT NULL
    );
    """,
    # 2 - full-text index over memories (skipped gracefully if FTS5 is missing)
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content, tags, project, content='memories', content_rowid='id'
    );
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, tags, project)
        VALUES (new.id, new.content, new.tags, new.project);
    END;
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
        VALUES ('delete', old.id, old.content, old.tags, old.project);
    END;
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
        VALUES ('delete', old.id, old.content, old.tags, old.project);
        INSERT INTO memories_fts(rowid, content, tags, project)
        VALUES (new.id, new.content, new.tags, new.project);
    END;
    """,
    # 3 - semantic code index
    """
    CREATE TABLE IF NOT EXISTS code_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo TEXT NOT NULL,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        mtime REAL NOT NULL,
        content TEXT NOT NULL,
        embedding BLOB
    );
    CREATE INDEX IF NOT EXISTS ix_code_repo ON code_chunks(repo, path);
    """,
]


class Database:
    """A single shared connection guarded by a re-entrant lock.

    SQLite in WAL mode handles the read/write mix of a desktop assistant
    comfortably; serialising through one connection avoids "database is
    locked" errors between worker threads.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.db_path()
        self._lock = threading.RLock()
        self.fts = False
        self.conn = self._open()
        self.migrate()

    def _open(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {ok}")
            return conn
        except sqlite3.DatabaseError as exc:
            log.error("database unusable (%s); attempting recovery", exc)
            if conn is not None:
                conn.close()  # Windows cannot move a file that is still open
            for suffix in ("-wal", "-shm"):
                side = self.path.with_name(self.path.name + suffix)
                if side.exists():
                    side.unlink(missing_ok=True)
            return self._recover()

    def _recover(self) -> sqlite3.Connection:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if self.path.exists():
            corrupt = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}.db")
            shutil.move(str(self.path), corrupt)
            log.error("moved corrupt database to %s", corrupt)
        backups = sorted(paths.backups_dir().glob("jarvis-*.db"), reverse=True)
        if backups:
            shutil.copy2(backups[0], self.path)
            log.warning("restored database from backup %s", backups[0].name)
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @property
    def version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        with self._lock:
            current = self.version
            for idx, script in enumerate(MIGRATIONS, start=1):
                if idx <= current:
                    continue
                try:
                    self.conn.executescript(script)
                except sqlite3.OperationalError as exc:
                    if "fts5" in str(exc).lower():
                        log.warning("FTS5 unavailable; memory search will use LIKE matching")
                    else:
                        raise
                self.conn.execute(f"PRAGMA user_version = {idx}")
                self.conn.commit()
                log.info("database migrated to version %d", idx)
            self.fts = bool(
                self.conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='memories_fts'"
                ).fetchone()
            )

    # ------------------------------------------------------------------ helpers
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def backup(self, dest: Path | None = None, keep: int = 10) -> Path:
        dest = dest or paths.backups_dir() / f"jarvis-{time.strftime('%Y%m%d-%H%M%S')}.db"
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()
        old = sorted(paths.backups_dir().glob("jarvis-*.db"), reverse=True)[keep:]
        for path in old:
            path.unlink(missing_ok=True)
        return dest

    def restore(self, source: Path) -> None:
        with self._lock:
            src = sqlite3.connect(str(source))
            try:
                src.backup(self.conn)
            finally:
                src.close()
            self.migrate()

    def integrity(self) -> str:
        with self._lock:
            return str(self.conn.execute("PRAGMA integrity_check").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self.conn.close()
