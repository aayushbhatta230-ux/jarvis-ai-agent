"""Developer Code Companion and Workspace File Inspector for JARVIS.

Inspects source code, counts syntax elements, and explains scripts concisely.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def analyze_code_file(filepath: str | Path) -> dict[str, Any]:
    """Inspect and extract structure from a code file."""
    p = Path(filepath)
    repo_root = Path(__file__).resolve().parent.parent

    if not p.is_file():
        # Check workspace root, tests, tools, core, interface
        candidates = [
            repo_root / filepath,
            repo_root / "tests" / filepath,
            repo_root / "tools" / filepath,
            repo_root / "core" / filepath,
            repo_root / "interface" / filepath,
        ]
        # Also check glob match in repo
        for c in candidates:
            if c.is_file():
                p = c
                break
        if not p.is_file():
            matches = list(repo_root.glob(f"**/{Path(filepath).name}"))
            if matches:
                p = matches[0]

    if not p.is_file():
        return {
            "ok": False,
            "spoken": f"I couldn't locate '{filepath}' in your workspace, sir.",
            "display": f"File not found: `{filepath}`",
        }

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "spoken": f"Could not read {p.name}: {exc}"}

    lines = content.splitlines()
    total_lines = len(lines)
    non_empty = [l for l in lines if l.strip()]
    comments = [l for l in non_empty if l.strip().startswith(("#", "//", "/*", "*"))]

    suffix = p.suffix.lower()
    functions: list[str] = []
    classes: list[str] = []

    if suffix in (".js", ".ts", ".jsx", ".tsx"):
        functions = re.findall(r"(?:function\s+([A-Za-z0-9_$]+)|const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)", content)
        classes = re.findall(r"class\s+([A-Za-z0-9_$]+)", content)
        fn_names = [f[0] or f[1] for f in functions if f[0] or f[1]]
    elif suffix == ".py":
        functions = re.findall(r"def\s+([A-Za-z0-9_]+)\s*\(", content)
        classes = re.findall(r"class\s+([A-Za-z0-9_]+)\s*[:\(]", content)
        fn_names = functions
    else:
        fn_names = []

    spoken = (
        f"In {p.name}, there are {total_lines} lines of code"
        + (f" with {len(fn_names)} function{'s' if len(fn_names) > 1 else ''}" if fn_names else "")
        + (f" and {len(classes)} class{'es' if len(classes) > 1 else ''}" if classes else "")
        + f", sir."
    )

    display = (
        f"💻 **Code Analysis: `{p.name}`**\n"
        f"- **Lines**: `{total_lines}` (Total), `{len(non_empty)}` (Code)\n"
        f"- **Functions**: `{len(fn_names)}`" + (f" ({', '.join(fn_names[:6])})" if fn_names else "") + "\n"
        f"- **Classes**: `{len(classes)}`" + (f" ({', '.join(classes[:4])})" if classes else "")
    )

    return {
        "ok": True,
        "filename": p.name,
        "path": str(p),
        "total_lines": total_lines,
        "function_count": len(fn_names),
        "functions": fn_names,
        "classes": classes,
        "spoken": spoken,
        "display": display,
    }
