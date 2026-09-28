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

# ---------------------------------------------------------------------- #
# Binary file kind labels ("what is it related to" for non-text files)
# ---------------------------------------------------------------------- #
INSTALLER_EXTS = {".exe", ".msi"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
INSTALLER_NAME_HINTS = (
    ("claude", "Claude desktop app"), ("chatgpt", "ChatGPT"), ("antigravity", "Antigravity"),
    ("vscode", "Visual Studio Code"), ("chrome", "Google Chrome"), ("firefox", "Firefox"),
    ("notion", "Notion"), ("discord", "Discord"), ("spotify", "Spotify"),
    ("obs", "OBS Studio"), ("vlc", "VLC"), ("zoom", "Zoom"), ("teams", "Microsoft Teams"),
    ("node", "Node.js"), ("python", "Python"), ("git", "Git"),
    ("cursor", "Cursor editor"), ("windsurf", "Windsurf"), ("postman", "Postman"),
    ("docker", "Docker Desktop"), ("virtualbox", "VirtualBox"), ("vmware", "VMware"),
)


def _clean_app_name(stem: str) -> str:
    """Turn 'Claude Setup' / 'Antigravity-x64' into 'Claude' / 'Antigravity'."""
    name = re.sub(r"[-_. ]?(setup|installer|install|x64|x86|win64|win32|amd64|v?\d[\d.]*)", "", stem, flags=re.I)
    return name.strip(" -_.") or stem.rsplit(" ", 1)[0].strip() or stem


def _summarise_archive(path: Path) -> str:
    """Peek inside a zip archive (central directory only — fast, stdlib)."""
    try:
        import zipfile
        with zipfile.ZipFile(str(path)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            return "It's an empty archive."
        if len(names) == 1:
            return f"It contains a single item: {names[0].split('/')[-1]}."
        tops = sorted({n.split("/")[0] for n in names})
        if len(tops) <= 3:
            return f"It contains {len(names)} files, mainly {', '.join(tops[:3])}."
        return f"It contains {len(names)} files across {len(tops)} folders."
    except Exception:
        return "It's an archive — I couldn't peek inside it."


def _summarise_image(path: Path) -> str:
    """Report image dimensions (lazy load — fast)."""
    try:
        from PIL import Image
        with Image.open(str(path)) as img:
            w, h = img.size
        megapixels = (w * h) / 1_000_000
        return f"It's an image, {w} by {h} pixels (about {megapixels:.1f} megapixels)."
    except Exception:
        return "It's an image file."


def _summarise_media(path: Path, kind: str) -> str:
    """Describe audio/video downloads (metadata-free — fast)."""
    return f"It's a {kind} file." if kind else "It's a media file."


def describe_file_relation(target: str | Path) -> dict[str, Any]:
    """Answer 'what is this file related to?' in one fast, natural sentence.

    Works for code, text, archives, images, media, installers and documents
    without an LLM round trip. Returns ``{"kind", "relation", "readable"}``.
    """
    path = Path(str(target)).expanduser()
    name = path.name
    stem = path.stem
    ext = path.suffix.lower()
    lowered = name.lower()

    # 1. Readable text/code files — content-aware via purpose inference.
    if ext in CODE_EXTENSIONS or ext in (".log", ".ini", ".cfg", ".toml", ".env"):
        try:
            raw = path.read_bytes()
            if b"\x00" not in raw[:2048]:
                text = raw[:65536].decode("utf-8", errors="replace")
                from tools.file_analysis import describe_purpose
                purpose = describe_purpose(ext, text, name)
                return {
                    "kind": "text",
                    "relation": f"It's related to {purpose[0].lower() + purpose[1:] if purpose else 'its contents'}.",
                    "readable": True,
                }
        except Exception:
            pass
        lang = CODE_EXTENSIONS.get(ext, "text file")
        return {"kind": "text", "relation": f"It's a {lang} source file.", "readable": True}

    # 2. Installers — match well-known apps, else clean the name.
    if ext in INSTALLER_EXTS:
        for hint, app in INSTALLER_NAME_HINTS:
            if hint in lowered:
                return {"kind": "installer", "relation": f"It's related to {app} — it looks like the installer for it.", "readable": False}
        return {"kind": "installer", "relation": f"It's an installer for {_clean_app_name(stem)}.", "readable": False}

    # 3. Archives — peek inside (zip central directory).
    if ext in ARCHIVE_EXTS:
        if ext == ".zip":
            return {"kind": "archive", "relation": _summarise_archive(path), "readable": False}
        return {"kind": "archive", "relation": "It's a compressed archive.", "readable": False}

    # 4. Images — dimensions.
    if ext in IMAGE_EXTS:
        return {"kind": "image", "relation": _summarise_image(path), "readable": False}

    # 5. Media — kind by extension.
    if ext in VIDEO_EXTS:
        label = {"mp4": "video", "mkv": "video", "avi": "video", "mov": "video clip", "webm": "web video"}.get(ext.lstrip("."), "video")
        return {"kind": "video", "relation": _summarise_media(path, label), "readable": False}
    if ext in AUDIO_EXTS:
        return {"kind": "audio", "relation": "It's an audio file.", "readable": False}

    # 6. Documents — kind by type.
    if ext == ".pdf":
        return {"kind": "document", "relation": "It's a PDF document.", "readable": False}
    if ext in (".docx", ".doc"):
        return {"kind": "document", "relation": "It's a Word document.", "readable": False}
    if ext in (".pptx", ".ppt"):
        return {"kind": "document", "relation": "It's a presentation file.", "readable": False}
    if ext in (".xlsx", ".xls"):
        return {"kind": "document", "relation": "It's a spreadsheet.", "readable": False}

    return {"kind": "file", "relation": f"It's a {ext.lstrip('.').upper() or 'miscellaneous'} file.", "readable": False}


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

        # Natural-language sentence about what the file is about (spoken reply).
        try:
            from tools.file_analysis import describe_purpose
            file_purpose = describe_purpose(ext, content, file_path.name)
        except Exception:
            file_purpose = summary
        if file_purpose and not file_purpose.endswith("."):
            file_purpose = file_purpose + "."
        low_purpose = (file_purpose or "").lower()
        if low_purpose.startswith("it ") or low_purpose.startswith("this "):
            purpose_sentence = "It's related to " + low_purpose.split(None, 1)[1].rstrip(".") + "."
        else:
            purpose_sentence = file_purpose or summary

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

        spoken_response = "Here's {name}, sir. {purpose}".format(
            name=file_path.name, purpose=purpose_sentence
        )

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

    spoken_parts = ["Your most recent downloaded file is {m} ({s}), downloaded {d}.".format(
        m=m_name, s=m_size, d=m_date
    )]
    try:
        recent_relation = describe_file_relation(most_recent[0]).get("relation", "")
    except Exception:
        recent_relation = ""
    if recent_relation:
        spoken_parts.append(recent_relation)
    if len(top_files) > 1:
        other_names = [f[0].name for f in top_files[1:3]]
        extra = ", and ".join(other_names)
        spoken_parts.append("Just before that, you grabbed {}.".format(extra))

    spoken = " ".join(spoken_parts)

    display_lines = ["📁 **Recent Downloads:**\n"]
    for i, (path, st) in enumerate(top_files, 1):
        line = "{n}. **{p}** ({s}) — *{d}*".format(
            n=i, p=path.name, s=_fmt_size(st.st_size), d=_fmt_date(st.st_mtime)
        )
        if i == 1 and recent_relation:
            line += "\n   ↳ {}".format(recent_relation)
        display_lines.append(line)

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

