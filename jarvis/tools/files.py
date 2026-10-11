"""Filesystem tools: inspect, read, write, edit, organise and open files."""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
from pathlib import Path
from typing import Any

from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
              "dist", "build", ".next", ".idea", "$RECYCLE.BIN", "AppData"}
TEXT_LIMIT = 200_000


def _abs(ctx: ToolContext, path: str, write: bool = False) -> Path:
    return ctx.app.permissions.check_path(os.path.expandvars(path), write=write)


def read_text_any(path: Path, max_chars: int = TEXT_LIMIT) -> str:
    """Read text from plain files, PDF and DOCX documents."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts = []
        for i, page in enumerate(reader.pages):
            parts.append(f"--- page {i + 1} ---\n{page.extract_text() or ''}")
            if sum(len(p) for p in parts) > max_chars:
                break
        return "\n".join(parts)[:max_chars]
    if suffix == ".docx":
        import docx

        d = docx.Document(str(path))
        return "\n".join(p.text for p in d.paragraphs)[:max_chars]
    if suffix in (".xlsx", ".xls", ".pptx", ".png", ".jpg", ".jpeg", ".exe", ".dll", ".zip"):
        raise ToolError(f"{suffix} files are binary; use a dedicated tool (e.g. analyze_screen for images)")
    raw = path.read_bytes()[: max_chars * 2]
    if b"\x00" in raw[:4096]:
        raise ToolError("file appears to be binary")
    for enc in ("utf-8", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)[:max_chars]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")[:max_chars]


@tool("list_directory", "List files and folders in a directory with sizes and modified times.",
      {"path": {"type": "string", "description": "Folder path; ~ and %VARS% allowed"},
       "pattern": {"type": "string", "description": "Optional glob filter like *.py"}},
      required=["path"], group="files")
def list_directory(ctx: ToolContext, path: str, pattern: str = "") -> dict[str, Any]:
    root = _abs(ctx, path)
    if not root.is_dir():
        raise ToolError(f"{root} is not a directory")
    entries = []
    for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
        if pattern and not fnmatch.fnmatch(p.name.lower(), pattern.lower()):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append({"name": p.name + ("/" if p.is_dir() else ""), "size": st.st_size if p.is_file() else None,
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
    return {"path": str(root), "count": len(entries), "entries": entries}


@tool("directory_tree", "Show a directory tree (skips .git, node_modules, venvs).",
      {"path": {"type": "string"}, "depth": {"type": "integer", "description": "Max depth, default 3"}},
      required=["path"], group="files")
def directory_tree(ctx: ToolContext, path: str, depth: int = 3) -> str:
    root = _abs(ctx, path)
    lines = [str(root)]
    count = 0

    def walk(d: Path, prefix: str, level: int) -> None:
        nonlocal count
        if level > depth or count > 400:
            return
        try:
            kids = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        kids = [k for k in kids if k.name not in _SKIP_DIRS]
        for i, k in enumerate(kids):
            count += 1
            if count > 400:
                lines.append(prefix + "... (truncated)")
                return
            last = i == len(kids) - 1
            lines.append(prefix + ("└── " if last else "├── ") + k.name + ("/" if k.is_dir() else ""))
            if k.is_dir():
                walk(k, prefix + ("    " if last else "│   "), level + 1)

    walk(root, "", 1)
    return "\n".join(lines)


@tool("read_file", "Read a text file, PDF or DOCX. Use start_line/end_line for large files.",
      {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
      required=["path"], group="files")
def read_file(ctx: ToolContext, path: str, start_line: int = 0, end_line: int = 0) -> ToolResult:
    p = _abs(ctx, path)
    if not p.is_file():
        raise ToolError(f"{p} does not exist or is not a file")
    text = read_text_any(p)
    lines = text.splitlines()
    total = len(lines)
    if start_line or end_line:
        s = max(1, start_line or 1)
        e = min(total, end_line or total)
        body = "\n".join(f"{n:5d}| {lines[n - 1]}" for n in range(s, e + 1))
        header = f"{p} (lines {s}-{e} of {total})"
    else:
        if len(text) > 60_000:
            body = "\n".join(f"{n:5d}| {line}" for n, line in enumerate(lines[:800], 1))
            header = f"{p} ({total} lines; showing 1-800 - request ranges for more)"
        else:
            body = "\n".join(f"{n:5d}| {line}" for n, line in enumerate(lines, 1))
            header = f"{p} ({total} lines)"
    return ToolResult(True, output=f"{header}\n{body}", data={"path": str(p), "lines": total})


def _summ_write(a: dict[str, Any]) -> str:
    return f"write {len(a.get('content', ''))} chars to {a.get('path')}"


@tool("write_file", "Create or overwrite a text file with the given content (creates parent folders).",
      {"path": {"type": "string"}, "content": {"type": "string"},
       "overwrite": {"type": "boolean", "description": "Must be true to replace an existing file"}},
      required=["path", "content"], category="write", group="files", summarize=_summ_write)
def write_file(ctx: ToolContext, path: str, content: str, overwrite: bool = False) -> str:
    p = _abs(ctx, path, write=True)
    if p.exists() and not overwrite:
        raise ToolError(f"{p} exists; pass overwrite=true to replace it (or use edit_file)")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        _snapshot(ctx, p)
    p.write_text(content, encoding="utf-8")
    written = p.read_text(encoding="utf-8")
    if written != content:
        raise ToolError("verification failed: file content differs after write")
    return f"wrote {len(content)} chars to {p} (verified)"


def _snapshot(ctx: ToolContext, p: Path) -> None:
    """Keep a pre-change copy so edits can be rolled back (see restore_file)."""
    from jarvis.core import paths

    snap_dir = paths.sub("snapshots") / time.strftime("%Y%m%d")
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / f"{int(time.time() * 1000)}__{p.name}"
    try:
        shutil.copy2(p, dest)
        ctx.app.db.kv_set(f"snapshot:{p}", str(dest))
    except OSError:
        pass


@tool("edit_file", "Replace an exact text snippet in a file. old_text must match exactly once "
      "unless replace_all is true. Read the file first.",
      {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
       "replace_all": {"type": "boolean"}},
      required=["path", "old_text", "new_text"], category="write", group="files",
      summarize=lambda a: f"edit {a.get('path')}")
def edit_file(ctx: ToolContext, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    p = _abs(ctx, path, write=True)
    text = p.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count == 0:
        # tolerate CRLF vs LF mismatches
        alt = old_text.replace("\n", "\r\n")
        if alt != old_text and text.count(alt) > 0:
            old_text, new_text, count = alt, new_text.replace("\n", "\r\n"), text.count(alt)
        else:
            raise ToolError("old_text not found; re-read the file and copy the exact text")
    if count > 1 and not replace_all:
        raise ToolError(f"old_text occurs {count} times; add surrounding context or set replace_all")
    _snapshot(ctx, p)
    updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    p.write_text(updated, encoding="utf-8")
    if new_text and new_text not in p.read_text(encoding="utf-8"):
        raise ToolError("verification failed after edit")
    return f"edited {p}: replaced {count if replace_all else 1} occurrence(s) (verified)"


@tool("restore_file", "Roll back the most recent edit/overwrite JARVIS made to a file.",
      {"path": {"type": "string"}}, required=["path"], category="write", group="files")
def restore_file(ctx: ToolContext, path: str) -> str:
    p = _abs(ctx, path, write=True)
    snap = ctx.app.db.kv_get(f"snapshot:{p}")
    if not snap or not Path(snap).exists():
        raise ToolError("no snapshot recorded for this file")
    shutil.copy2(snap, p)
    return f"restored {p} from snapshot {Path(snap).name}"


@tool("make_directory", "Create a folder (and parents).", {"path": {"type": "string"}},
      required=["path"], category="write", group="files")
def make_directory(ctx: ToolContext, path: str) -> str:
    p = _abs(ctx, path, write=True)
    p.mkdir(parents=True, exist_ok=True)
    return f"folder ready: {p}"


@tool("move_path", "Move or rename a file/folder. Will not overwrite an existing destination.",
      {"source": {"type": "string"}, "destination": {"type": "string"}},
      required=["source", "destination"], category="write", group="files")
def move_path(ctx: ToolContext, source: str, destination: str) -> str:
    src = _abs(ctx, source, write=True)
    dst = _abs(ctx, destination, write=True)
    if not src.exists():
        raise ToolError(f"{src} does not exist")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"destination {dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    if not dst.exists():
        raise ToolError("move could not be verified")
    return f"moved {src} -> {dst}"


@tool("copy_path", "Copy a file or folder.", {"source": {"type": "string"}, "destination": {"type": "string"}},
      required=["source", "destination"], category="write", group="files")
def copy_path(ctx: ToolContext, source: str, destination: str) -> str:
    src = _abs(ctx, source)
    dst = _abs(ctx, destination, write=True)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"destination {dst} already exists")
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"copied {src} -> {dst}"


@tool("delete_path", "Move a file or folder to the Recycle Bin (recoverable).", {"path": {"type": "string"}},
      required=["path"], category="destructive", group="files",
      summarize=lambda a: f"move {a.get('path')} to the Recycle Bin")
def delete_path(ctx: ToolContext, path: str) -> str:
    from send2trash import send2trash

    p = _abs(ctx, path, write=True)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    if p == Path.home() or len(p.parts) <= 2:
        raise ToolError("refusing to delete a home or drive root folder")
    send2trash(str(p))
    if p.exists():
        raise ToolError("item still exists after delete")
    return f"moved {p} to the Recycle Bin"


@tool("search_files", "Find files by name pattern (glob) and/or text content under a folder. "
      "Sorted by most recently modified.",
      {"root": {"type": "string", "description": "Folder to search, default home"},
       "name": {"type": "string", "description": "Glob like *.pdf or *report*"},
       "contains": {"type": "string", "description": "Optional text that must appear in the file"},
       "modified_within_days": {"type": "number"},
       "limit": {"type": "integer"}},
      group="files", timeout=90)
def search_files(ctx: ToolContext, root: str = "~", name: str = "*", contains: str = "",
                 modified_within_days: float = 0, limit: int = 40) -> dict[str, Any]:
    base = _abs(ctx, root)
    cutoff = time.time() - modified_within_days * 86400 if modified_within_days else 0
    hits: list[tuple[float, str, int]] = []
    pat = name.lower() if any(c in name for c in "*?[") else f"*{name.lower()}*"
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(base):
        ctx.check()
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            scanned += 1
            if not fnmatch.fnmatch(fn.lower(), pat):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if cutoff and st.st_mtime < cutoff:
                continue
            if contains:
                if st.st_size > 5_000_000:
                    continue
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as fh:
                        if contains.lower() not in fh.read().lower():
                            continue
                except OSError:
                    continue
            hits.append((st.st_mtime, fp, st.st_size))
        if scanned > 300_000:
            break
    hits.sort(reverse=True)
    return {
        "root": str(base),
        "matches": [
            {"path": p, "size": s, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(m))}
            for m, p, s in hits[:limit]
        ],
        "total_matches": len(hits),
    }


@tool("open_path", "Open a file or folder with its default Windows application.", {"path": {"type": "string"}},
      required=["path"], category="execute", group="files")
def open_path(ctx: ToolContext, path: str) -> str:
    p = _abs(ctx, path)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    os.startfile(str(p))  # type: ignore[attr-defined]
    return f"opened {p}"
