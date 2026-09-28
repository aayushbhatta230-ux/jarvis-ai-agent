"""
File Authoring for JARVIS
=========================
Create new files, write content into them, append, and delete.

This is deliberately separate from :mod:`tools.smart_files` (which only reads),
so that every write goes through one auditable place: mutations are sandboxed
to the user's own home/project directories, overwriting requires an explicit
``overwrite=True``, and each result carries a spoken confirmation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Locations we are willing to write into. Anything outside is rejected so a
# mistaken phrase can never clobber a system file.
_SAFE_ROOTS = tuple(
    Path(p).expanduser()
    for p in (
        os.environ.get("USERPROFILE", str(Path.home())),
        str(Path.cwd()),
    )
)

_MAX_WRITE_BYTES = 2_000_000  # 2 MB guard against runaway generation

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "javascript",
    ".tsx": "typescript", ".html": "html", ".htm": "html", ".css": "css",
    ".json": "json", ".md": "markdown", ".txt": "text", ".csv": "csv",
    ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".sh": "bash",
    ".bat": "batch", ".ps1": "powershell", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".java": "java", ".sql": "sql",
}


def _is_within_safe_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in _SAFE_ROOTS:
        try:
            if resolved == root or root in resolved.parents:
                return True
        except OSError:
            continue
    return False


def resolve_target_path(name: str) -> Path:
    """Turn a user-supplied filename into an absolute path we may write to.

    Bare names (``notes.txt``) land in Documents so they are easy to find;
    anything containing a separator is treated as a path.
    """
    raw = (name or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("No file name was provided.")

    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        docs = Path(os.path.expandvars(r"%USERPROFILE%")) / "Documents"
        candidate = (docs if docs.exists() else Path.home()) / candidate
    return candidate


def _preview(content: str, limit: int = 400) -> str:
    body = content.strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + f"\n… (+{len(body) - limit} more characters)"


def create_file(name: str, content: str = "", overwrite: bool = False) -> dict[str, Any]:
    """Create (or overwrite) a text file with ``content``."""
    try:
        path = resolve_target_path(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not _is_within_safe_root(path):
        return {
            "ok": False,
            "error": "I only write inside your home folder or the project directory.",
        }

    if len(content.encode("utf-8", errors="ignore")) > _MAX_WRITE_BYTES:
        return {"ok": False, "error": "That content is too large to write in one go."}

    existed = path.exists()
    if existed and not overwrite:
        return {
            "ok": False,
            "error": f"{path.name} already exists. Say 'overwrite it' if you want me to replace it.",
            "path": str(path),
            "exists": True,
        }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - surface any IO problem
        return {"ok": False, "error": f"Couldn't write the file: {exc}", "path": str(path)}

    lines = content.count("\n") + 1 if content else 0
    size = path.stat().st_size
    body = f"\n\n---\n{_preview(content)}\n---" if content else "\n\n_(the file is empty)_"

    return {
        "ok": True,
        "action": "overwritten" if existed else "created",
        "path": str(path),
        "name": path.name,
        "lines": lines,
        "bytes": size,
        "language": _LANG_BY_EXT.get(path.suffix.lower(), "text"),
        "spoken": (
            f"{'Overwrote' if existed else 'Created'} {path.name} with {lines} "
            f"line{'' if lines == 1 else 's'}, sir."
        ),
        "markdown": (
            f"📝 **{'Overwrote' if existed else 'Created'} `{path.name}`** "
            f"&nbsp;•&nbsp; {lines} lines &nbsp;•&nbsp; {size:,} bytes\n\n"
            f"`{path}`{body}"
        ),
    }


def append_to_file(name: str, content: str) -> dict[str, Any]:
    """Append ``content`` to a file, creating it if it does not exist."""
    try:
        path = resolve_target_path(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not _is_within_safe_root(path):
        return {"ok": False, "error": "I only write inside your home folder or the project directory."}

    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if (not existed or path.stat().st_size == 0) else os.linesep
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + content)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't append to the file: {exc}", "path": str(path)}

    total = path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    return {
        "ok": True,
        "action": "appended",
        "path": str(path),
        "name": path.name,
        "lines": total,
        "spoken": f"Appended to {path.name}, sir.",
        "markdown": f"📝 **Appended to `{path.name}`** - now {total} lines.\n\n`{path}`",
    }


def delete_file(name: str) -> dict[str, Any]:
    """Move a file to the Recycle Bin when possible, else remove it."""
    try:
        path = resolve_target_path(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not _is_within_safe_root(path) or not path.exists():
        return {"ok": False, "error": "I couldn't find that file to delete."}

    try:
        import send2trash  # type: ignore
        send2trash.send2trash(str(path))
        how = "moved to the Recycle Bin"
    except Exception:  # noqa: BLE001 - fall back to a plain delete
        try:
            path.unlink()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Couldn't delete the file: {exc}"}
        how = "deleted"

    return {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "spoken": f"{path.name} was {how}, sir.",
        "markdown": f"🗑️ **{path.name}** was {how}.",
    }


def parse_quoted_create(text: str) -> tuple[str | None, str | None]:
    """Parse ``create a file "notes.txt" containing "hello world"``.

    Returns ``(filename, content)``, or ``(None, None)`` when the phrase does
    not follow that shape.
    """
    m = re.search(
        r'["“](.+?)["”]\s*(?:containing|with|that says|which says|saying)\s*["“]?(.*?)["”]?\s*$',
        text, flags=re.I | re.S,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    m2 = re.search(
        r'\bfile\s+(?:named|called)?\s*["“]?([\w\-. ]+?\.[A-Za-z0-9]{1,6})["”]?\s+'
        r'(?:with|containing)\s+(?:the\s+)?(?:content|contents|text|words)?\s*[:\-]?\s*["“]?(.*?)["”]?\s*$',
        text, flags=re.I | re.S,
    )
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()

    return None, None


__all__ = [
    "create_file",
    "append_to_file",
    "delete_file",
    "resolve_target_path",
    "parse_quoted_create",
]
