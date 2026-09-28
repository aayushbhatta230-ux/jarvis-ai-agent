"""
Smart File Intelligence for JARVIS
===================================
Self-intelligence to locate, inspect, read, display, and summarize
files across the workspace, project directories, and user system.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Supported text extensions for direct display & code formatting
CODE_EXTENSIONS = {
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".py": "python",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
    ".csv": "csv",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
    ".bat": "batch",
    ".ps1": "powershell",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".java": "java",
    ".sql": "sql",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".idea",
    ".vscode",
}


def clean_file_query(query: str) -> str:
    """Extract clean filename from conversational request."""
    text = (query or "").strip()
    # Strip common leading verbs/phrases
    text = re.sub(r"^(?:please\s+|can\s+you\s+|could\s+you\s+|jarvis\s+)?", "", text, flags=re.I)
    text = re.sub(r"^(?:read|open|show(?:\s+me)?|view|display|check|inspect|tell\s+me\s+what(?:'s|\s+is)\s+in|what(?:'s|\s+is)\s+in|what(?:'s|\s+is)\s+inside|contents\s+of|summarize)\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:the\s+|a\s+|this\s+|that\s+|my\s+)?(?:file\s+)?", "", text, flags=re.I)
    text = re.sub(r"\s+(?:file|document|code|script)$", "", text, flags=re.I)
    # Strip quotes
    text = text.strip("'\"` \t\r\n.,;?!")
    # If there's a file pattern in the text (like test_dom.js or app.py)
    match = re.search(r"([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]{1,8})", text)
    if match:
        return match.group(1).strip()
    # Return first token or cleaned string
    tokens = text.split()
    if tokens:
        return tokens[0].strip("'\"` \t\r\n.,;?!")
    return text


def find_file(target: str) -> Path | None:
    """Intelligently locate a file anywhere in project, workspace, or user folders."""
    cleaned = clean_file_query(target)
    if not cleaned:
        return None

    # 1. Direct path check
    direct = Path(cleaned)
    if direct.is_file():
        return direct.resolve()

    # If user provided a path with drive letter or backslashes
    if ":" in cleaned or "\\" in cleaned or "/" in cleaned:
        p = Path(cleaned).expanduser().resolve()
        if p.is_file():
            return p

    search_roots = [
        PROJECT_ROOT,
        PROJECT_ROOT / "interface",
        PROJECT_ROOT / "core",
        PROJECT_ROOT / "tools",
        PROJECT_ROOT / "voice",
        PROJECT_ROOT / "vision",
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
        Path.home(),
    ]

    has_ext = bool(re.search(r"\.[a-zA-Z0-9]{1,6}$", cleaned))
    extensions_to_try = [""] if has_ext else [".js", ".py", ".html", ".css", ".json", ".txt", ".md", ".ts"]

    # 2. Check direct children of search roots
    for root in search_roots:
        if not root.is_dir():
            continue
        for ext in extensions_to_try:
            candidate_name = f"{cleaned}{ext}".lower()
            try:
                for entry in root.iterdir():
                    if entry.is_file() and entry.name.lower() == candidate_name:
                        return entry.resolve()
            except Exception:
                continue

    # 3. Recursive search in PROJECT_ROOT (up to depth 4, skipping EXCLUDE_DIRS)
    for ext in extensions_to_try:
        candidate_name = f"{cleaned}{ext}".lower()
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                if f.lower() == candidate_name:
                    return (Path(root) / f).resolve()

    # 4. Fuzzy / prefix match in PROJECT_ROOT
    clean_lower = cleaned.lower()
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for f in files:
            f_lower = f.lower()
            if clean_lower in f_lower:
                return (Path(root) / f).resolve()

    # 5. Check Desktop, Documents, Downloads recursively (depth 2)
    for folder in ("Desktop", "Documents", "Downloads"):
        user_folder = Path.home() / folder
        if not user_folder.is_dir():
            continue
        try:
            for item in user_folder.rglob("*"):
                if item.is_file():
                    if item.name.lower() == cleaned.lower():
                        return item.resolve()
                    if not has_ext and item.stem.lower() == cleaned.lower():
                        return item.resolve()
        except Exception:
            continue

    return None


def inspect_and_read_file(target: str, max_chars: int = 12000) -> dict[str, Any]:
    """Find, read, display, and summarize file content."""
    file_path = find_file(target)
    if not file_path or not file_path.is_file():
        clean_target = clean_file_query(target)
        return {
            "success": False,
            "filename": clean_target,
            "path": None,
            "error": f"I couldn't locate '{clean_target}'. Please specify the folder or exact filename.",
            "spoken": f"I couldn't locate '{clean_target}', sir. Could you double-check the filename?",
            "display": f"⚠️ Could not find file: `{clean_target}`",
        }

    try:
        raw_bytes = file_path.read_bytes()
        # Simple binary check
        if b"\x00" in raw_bytes[:1024]:
            return {
                "success": False,
                "filename": file_path.name,
                "path": str(file_path),
                "error": f"'{file_path.name}' appears to be a binary file and cannot be read as text.",
                "spoken": f"'{file_path.name}' is a binary file, sir, so I cannot read its text directly.",
                "display": f"⚠️ Cannot display binary file: `{file_path.name}`",
            }
        
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = raw_bytes.decode("latin-1", errors="replace")

        lines = content.splitlines()
        line_count = len(lines)
        ext = file_path.suffix.lower()
        lang = CODE_EXTENSIONS.get(ext, ext.lstrip(".") or "text")

        # Extract brief description / summary
        summary = _extract_file_summary(file_path.name, ext, lines, content)

        # Prepare code preview for chat display
        preview_text = content
        if len(preview_text) > max_chars:
            preview_text = preview_text[:max_chars] + f"\n\n// ... ({line_count} total lines, preview truncated for display)"

        display_markdown = (
            f"📁 **`{file_path.name}`** &nbsp;•&nbsp; `{line_count}` lines &nbsp;•&nbsp; `{file_path.stat().st_size:,} bytes`\n\n"
            f"*{summary}*\n\n"
            f"```{lang}\n"
            f"{preview_text}\n"
            f"```"
        )

        spoken_response = f"Here is {file_path.name}, sir. {summary}"

        # Park the file as the active working context so follow-up questions
        # ("what is it about?", "suggest changes") are grounded in real content.
        try:
            from core.file_session import get_file_session
            get_file_session().set(
                path=str(file_path),
                name=file_path.name,
                ext=ext,
                language=lang,
                lines=line_count,
                size_bytes=file_path.stat().st_size,
                content=content,
                summary=summary,
                source="read",
            )
        except Exception as session_err:  # noqa: BLE001 - session is advisory
            print(f"[FILE SESSION] {session_err}")

        return {
            "success": True,
            "filename": file_path.name,
            "path": str(file_path),
            "ext": ext,
            "language": lang,
            "lines": line_count,
            "size_bytes": file_path.stat().st_size,
            "content": content,
            "preview": preview_text,
            "summary": summary,
            "spoken": spoken_response,
            "display": display_markdown,
        }
    except Exception as exc:
        return {
            "success": False,
            "filename": file_path.name,
            "path": str(file_path),
            "error": str(exc),
            "spoken": f"I encountered an error reading {file_path.name}: {exc}",
            "display": f"⚠️ Error reading `{file_path.name}`: {exc}",
        }


def _extract_file_summary(filename: str, ext: str, lines: list[str], content: str) -> str:
    """Extract a fast, intelligent 1-2 sentence overview of what the file contains."""
    clean_lines = [l.strip() for l in lines if l.strip()]
    if not clean_lines:
        return "This file is currently empty."

    # Look for top docstring or comments
    doc_comments = []
    for line in clean_lines[:15]:
        if line.startswith(("//", "#", "/*", "*", "'''", '"""')):
            clean_c = re.sub(r"^[/\\*#'\"]+\s*", "", line).strip()
            if clean_c and len(clean_c) > 6:
                doc_comments.append(clean_c)

    if doc_comments:
        return doc_comments[0].rstrip(".") + "."

    # Check for class or function definitions
    defs = []
    for line in clean_lines:
        m = re.search(r"^(?:export\s+)?(?:class|function|def|const|var|let)\s+([A-Za-z0-9_]+)", line)
        if m:
            defs.append(m.group(1))
        if len(defs) >= 3:
            break

    if defs:
        items = ", ".join(f"'{d}'" for d in defs[:3])
        return f"It contains {len(lines)} lines defining {items}."

    return f"It is a {len(lines)}-line {ext.lstrip('.').upper() or 'text'} file."


def list_project_files(folder_query: str | None = None) -> dict[str, Any]:
    """Scan and list files across workspace or specific directory with rich metadata."""
    target_path = PROJECT_ROOT
    folder_label = "JARVIS Workspace"

    if folder_query:
        fq_lower = folder_query.lower()
        if "desktop" in fq_lower:
            target_path = Path.home() / "Desktop"
            folder_label = "Desktop"
        elif "document" in fq_lower:
            target_path = Path.home() / "Documents"
            folder_label = "Documents"
        elif "download" in fq_lower:
            target_path = Path.home() / "Downloads"
            folder_label = "Downloads"
        elif "interface" in fq_lower:
            target_path = PROJECT_ROOT / "interface"
            folder_label = "interface/"
        elif "core" in fq_lower:
            target_path = PROJECT_ROOT / "core"
            folder_label = "core/"
        elif "tools" in fq_lower:
            target_path = PROJECT_ROOT / "tools"
            folder_label = "tools/"

    if not target_path.is_dir():
        target_path = PROJECT_ROOT

    found_files = []
    # Collect key files up to 3 levels deep
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        # Limit depth
        rel_depth = len(Path(root).relative_to(target_path).parts)
        if rel_depth > 2:
            continue
        for f in files:
            if f.startswith((".", "~")) or f.endswith((".pyc", ".tmp", ".log")):
                continue
            fpath = Path(root) / f
            try:
                rel_path = fpath.relative_to(target_path)
                size_bytes = fpath.stat().st_size
                ext = fpath.suffix.lower()
                is_code = ext in CODE_EXTENSIONS
                found_files.append({
                    "name": f,
                    "rel_path": str(rel_path).replace("\\", "/"),
                    "abs_path": str(fpath),
                    "size_bytes": size_bytes,
                    "ext": ext,
                    "is_code": is_code,
                })
            except Exception:
                continue

    # Sort files: code files first, then by name
    found_files.sort(key=lambda x: (not x["is_code"], x["rel_path"].count("/"), x["name"]))

    if not found_files:
        return {
            "success": True,
            "count": 0,
            "folder": folder_label,
            "files": [],
            "display": f"📁 **{folder_label}**\n\nNo files found in this directory.",
            "spoken": f"I didn't find any files in {folder_label}, sir.",
        }

    # Format Markdown
    file_lines = []
    for item in found_files[:20]:
        sz_kb = max(1, item["size_bytes"] // 1024)
        icon = "📄"
        if item["ext"] in (".js", ".ts"):
            icon = "⚡"
        elif item["ext"] == ".py":
            icon = "🐍"
        elif item["ext"] in (".html", ".css"):
            icon = "🌐"
        elif item["ext"] == ".json":
            icon = "📦"
        elif item["ext"] in (".png", ".jpg", ".ico"):
            icon = "🖼️"
        file_lines.append(f"- {icon} **`{item['rel_path']}`** &nbsp;•&nbsp; `{sz_kb} KB`")

    if len(found_files) > 20:
        file_lines.append(f"\n*...and {len(found_files) - 20} more files.*")

    display_md = (
        f"📁 **{folder_label} Files** &nbsp;(`{len(found_files)} items found`)\n\n"
        + "\n".join(file_lines)
        + "\n\n💡 *Tip: Ask `read <filename>` or tap a file chip to view and inspect its code.*"
    )

    # Highlight top 3 files in spoken response
    top_names = [f["name"] for f in found_files[:3]]
    top_str = ", ".join(top_names)
    spoken = f"Here are the files in {folder_label}, sir. I found {len(found_files)} files, including {top_str}."

    return {
        "success": True,
        "count": len(found_files),
        "folder": folder_label,
        "files": found_files[:30],
        "display": display_md,
        "spoken": spoken,
    }


def get_recent_downloads(limit: int = 5) -> dict[str, Any]:
    """Retrieve and naturally describe recently downloaded files from the Downloads folder."""
    from datetime import datetime
    user_home = Path.home()
    candidates = [
        user_home / "Downloads",
        user_home / "OneDrive" / "Downloads",
        Path("C:/Users/joshi/Downloads"),
    ]
    downloads_dir = None
    for c in candidates:
        if c.is_dir():
            downloads_dir = c
            break

    if not downloads_dir:
        return {
            "success": False,
            "spoken": "I could not locate your Downloads folder, sir.",
            "display": "⚠️ Could not locate Downloads directory.",
            "files": []
        }

    valid_files: list[tuple[Path, os.stat_result]] = []
    try:
        for entry in downloads_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name.startswith((".", "~")) or name.lower() == "desktop.ini":
                continue
            ext = entry.suffix.lower()
            if ext in (".tmp", ".crdownload", ".part", ".download"):
                continue
            try:
                st = entry.stat()
                valid_files.append((entry, st))
            except OSError:
                continue
    except Exception as exc:
        return {
            "success": False,
            "spoken": f"I had trouble reading the Downloads folder: {exc}",
            "display": f"⚠️ Error accessing Downloads: {exc}",
            "files": []
        }

    if not valid_files:
        return {
            "success": True,
            "spoken": "Your Downloads folder is currently empty, sir.",
            "display": "📁 **Downloads folder is empty.**",
            "files": []
        }

    valid_files.sort(key=lambda item: item[1].st_mtime, reverse=True)
    top_files = valid_files[:limit]

    def _fmt_size(sz: int) -> str:
        if sz < 1024:
            return f"{sz} B"
        elif sz < 1024 * 1024:
            return f"{sz / 1024:.1f} KB"
        elif sz < 1024 * 1024 * 1024:
            return f"{sz / (1024 * 1024):.1f} MB"
        return f"{sz / (1024 * 1024 * 1024):.1f} GB"

    def _fmt_date(mtime: float) -> str:
        dt = datetime.fromtimestamp(mtime)
        now = datetime.now()
        diff = now - dt
        if diff.days == 0:
            return f"today at {dt.strftime('%I:%M %p').lstrip('0')}"
        elif diff.days == 1:
            return f"yesterday at {dt.strftime('%I:%M %p').lstrip('0')}"
        elif diff.days < 7:
            return f"on {dt.strftime('%A at %I:%M %p').lstrip('0')}"
        return f"on {dt.strftime('%b %d')}"

    most_recent = top_files[0]
    m_name = most_recent[0].name
    m_size = _fmt_size(most_recent[1].st_size)
    m_date = _fmt_date(most_recent[1].st_mtime)

    spoken_parts = [f"Your most recent downloaded file is {m_name} ({m_size}), downloaded {m_date}."]
    if len(top_files) > 1:
        other_names = [f[0].name for f in top_files[1:3]]
        spoken_parts.append(f"You also recently downloaded {', and '.join(other_names)}.")

    spoken = " ".join(spoken_parts)

    display_lines = ["📁 **Recent Downloads:**\n"]
    for i, (path, st) in enumerate(top_files, 1):
        display_lines.append(f"{i}. **{path.name}** ({_fmt_size(st.st_size)}) &mdash; *{_fmt_date(st.st_mtime)}*")

    display = "\n".join(display_lines)

    return {
        "success": True,
        "spoken": spoken,
        "display": display,
        "most_recent_path": str(most_recent[0]),
        "files": [{"path": str(p), "name": p.name, "size": st.st_size} for p, st in top_files]
    }


def get_recent_user_files(limit: int = 5) -> dict[str, Any]:
    """Retrieve recent user files across Desktop, Documents, Downloads, and Projects."""
    from datetime import datetime
    user_home = Path.home()
    search_dirs = [
        user_home / "Desktop",
        user_home / "Documents",
        user_home / "Downloads",
        user_home / "Projects",
        PROJECT_ROOT,
    ]

    all_files: list[tuple[Path, os.stat_result]] = []
    ignored_exts = {".log", ".db", ".vscdb", ".sqlite", ".tmp", ".crdownload", ".bak", ".ini"}
    ignored_parts = {"appdata", ".vscode", ".git", "node_modules", "__pycache__", "breadcrumbs"}

    for s_dir in search_dirs:
        if not s_dir.is_dir():
            continue
        try:
            for root, dirs, files in os.walk(s_dir):
                # Prune hidden & system dirs
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ignored_parts]
                if any(ig in root.lower() for ig in ignored_parts):
                    continue
                for fname in files:
                    if fname.startswith((".", "~")):
                        continue
                    p = Path(root) / fname
                    if p.suffix.lower() in ignored_exts:
                        continue
                    try:
                        st = p.stat()
                        all_files.append((p, st))
                    except OSError:
                        continue
        except Exception:
            continue

    if not all_files:
        return {
            "success": True,
            "spoken": "I did not find any recently modified project or personal files, sir.",
            "display": "📁 **No recent files found.**",
            "files": []
        }

    all_files.sort(key=lambda item: item[1].st_mtime, reverse=True)
    top_files = all_files[:limit]

    def _fmt_size(sz: int) -> str:
        if sz < 1024:
            return f"{sz} B"
        elif sz < 1024 * 1024:
            return f"{sz / 1024:.1f} KB"
        return f"{sz / (1024 * 1024):.1f} MB"

    def _fmt_date(mtime: float) -> str:
        dt = datetime.fromtimestamp(mtime)
        now = datetime.now()
        diff = now - dt
        if diff.days == 0:
            return f"today at {dt.strftime('%I:%M %p').lstrip('0')}"
        elif diff.days == 1:
            return f"yesterday at {dt.strftime('%I:%M %p').lstrip('0')}"
        return f"on {dt.strftime('%b %d')}"

    most_recent = top_files[0]
    spoken = f"Your most recently active file is {most_recent[0].name}, modified {_fmt_date(most_recent[1].st_mtime)}."
    if len(top_files) > 1:
        spoken += f" Other recent files include {', and '.join(f[0].name for f in top_files[1:3])}."

    display_lines = ["📁 **Recently Active Files:**\n"]
    for i, (path, st) in enumerate(top_files, 1):
        display_lines.append(f"{i}. **{path.name}** ({_fmt_size(st.st_size)}) &mdash; *{_fmt_date(st.st_mtime)}*")

    display = "\n".join(display_lines)

    return {
        "success": True,
        "spoken": spoken,
        "display": display,
        "most_recent_path": str(most_recent[0]),
        "files": [{"path": str(p), "name": p.name, "size": st.st_size} for p, st in top_files]
    }

