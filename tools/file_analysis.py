"""Deterministic file analysis, purpose inference and content extraction.

These helpers give JARVIS grounded answers about a file (what it does, what
needs fixing, what it contains) without spending an LLM round trip — and they
feed structured facts to the model when a deeper review is requested.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------- #
# Language patterns
# ---------------------------------------------------------------------- #
PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)
PY_CLASS = re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.M)
PY_IMPORT = re.compile(r"^\s*(?:from\s+[\w.]+\s+import|import\s+[\w.]+)", re.M)
JS_FUNC = re.compile(
	r"(?:function\s+([A-Za-z_$][\w$]*)\s*\()|"
	r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
	re.M,
)
JS_CLASS = re.compile(r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
JS_METHOD = re.compile(r"^\s*(?:async\s+|static\s+|\*\s*)*([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*\{", re.M)
JS_KEYWORDS = {
	"if", "for", "while", "switch", "catch", "return", "else", "do", "try",
	"function", "constructor", "typeof", "new", "await", "case", "throw",
}


def js_functions(text: str) -> list[str]:
	"""Function names + class methods for JS/TS sources (control flow excluded)."""
	names: list[str] = [a or b for a, b in JS_FUNC.findall(text)]
	for name in JS_METHOD.findall(text):
		if name.lower() in JS_KEYWORDS or name in names:
			continue
		names.append(name)
	return names
JS_IMPORT = re.compile(r"^\s*(?:import\s+.*from\s+['\"]|require\(['\"])", re.M)
C_FUNC = re.compile(r"^[A-Za-z_][\w\s\*]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$", re.M)
C_INCLUDE = re.compile(r"^\s*#include\s+[<\"]", re.M)
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b")
URL_RE = re.compile(r"https?://[^\s'\"<>)]+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
HEADING_MD = re.compile(r"^#{1,6}\s+(.+)$", re.M)
DOCSTRING_RE = re.compile(r'^\s*(?:"""|\'\'\')', re.M)

TEXT_EXTS = {".md", ".txt", ".rst", ".log", ".csv", ".json", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".toml"}


def _tidy(text: str) -> str:
	clean = re.sub(r"\s+", " ", (text or "")).strip()
	if len(clean) > 260:
		clean = clean[:257].rstrip() + "..."
	return clean[0].upper() + clean[1:] if clean else clean


def describe_purpose(ext: str, content: str, name: str = "") -> str:
	"""Return a plain-English description of what a file is about."""
	ext = (ext or Path(name).suffix or "").lower()
	text = content or ""
	lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
	if not lines:
		return "This file is empty."

	# 1. Module docstring or leading comment block
	m = re.search(r'^\s*(?:"""|\'\'\')(.*?)\1', text, re.S)
	if m and len(m.group(1).strip()) > 12:
		return _tidy(m.group(1))
	if lines[0].startswith(("#", "//", "/*", "*")):
		comment_lines = []
		for ln in lines[:8]:
			clean = re.sub(r"^(#+|//+|/\*+|\*)\s?", "", ln).strip().rstrip("*/").strip()
			if len(clean) > 8 and not clean.startswith(("=", "-")):
				comment_lines.append(clean)
			if len(comment_lines) >= 2:
				break
		if comment_lines:
			joined = comment_lines[0]
			for nxt in comment_lines[1:]:
				if not joined.rstrip().endswith((".", "!", "?", ":", ";")):
					joined = joined.rstrip() + "."
				joined = joined + " " + nxt.lstrip()
			return _tidy(joined)

	# 2. Markup titles
	if ext in (".html", ".htm", ".xml"):
		t = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
		if t:
			return f"Web document titled '{t.group(1).strip()}'."
	if ext == ".md":
		h = re.search(r"^#\s+(.+)$", text, re.M)
		if h:
			return f"Markdown document titled '{h.group(1).strip()}'."

	# 3. Definition-based
	if ext == ".py":
		defs = PY_DEF.findall(text)[:6]
		classes = PY_CLASS.findall(text)[:4]
		parts = []
		if classes:
			parts.append(f"defines {len(classes)} class{'es' if len(classes) > 1 else ''} ({', '.join(classes[:3])})")
		if defs:
			parts.append(f"exposes functions like {', '.join(defs[:3])}")
		if parts:
			return "Python module that " + " and ".join(parts) + "."
		return "Python module with script-level logic."
	if ext in (".js", ".ts", ".jsx", ".tsx"):
		funcs = js_functions(text)[:6]
		classes = JS_CLASS.findall(text)[:3]
		parts = []
		if classes:
			parts.append(f"defines {', '.join(classes)}")
		if funcs:
			parts.append(f"defines functions such as {', '.join(funcs[:3])}")
		if parts:
			return "JavaScript/TypeScript module that " + " and ".join(parts) + "."
		return "JavaScript module with top-level logic."

	# 4. Data files
	if ext == ".json":
		return f"JSON data file with {len(lines)} lines."
	if ext == ".csv":
		return f"CSV data table with columns: {lines[0][:120]}."

	return f"{ext.lstrip('.') or 'Text'} file containing {len(lines)} lines."



def _read_text(path: Path) -> str | None:
	"""Return file text, or None for binary/unreadable files."""
	try:
		raw = path.read_bytes()
	except Exception:
		return None
	if b"\x00" in raw[:2048]:
		return None
	try:
		return raw.decode("utf-8")
	except UnicodeDecodeError:
		return raw.decode("latin-1", errors="replace")


def analyze_file(target: str | Path) -> dict[str, Any]:
	"""Analyze a file: purpose, metrics, findings, and concrete suggested changes."""
	from tools.smart_files import find_file

	if isinstance(target, Path):
		path = target
	else:
		path = find_file(str(target)) or Path(str(target)).expanduser()
	if path is None or not path.is_file():
		return {
			"ok": False,
			"spoken": f"I couldn't find '{target}' to analyze. Tell me the filename, or open it first.",
		}

	text = _read_text(path)
	if text is None:
		return {"ok": False, "spoken": f"{path.name} is a binary file, so I can't analyze its source."}

	ext = path.suffix.lower()
	lines = text.splitlines()
	line_count = len(lines)
	non_empty = [ln for ln in lines if ln.strip()]
	purpose = describe_purpose(ext, text, path.name)

	defs: list[str] = []
	classes: list[str] = []
	imports = 0
	if ext == ".py":
		defs = PY_DEF.findall(text)
		classes = PY_CLASS.findall(text)
		imports = len(PY_IMPORT.findall(text))
	elif ext in (".js", ".ts", ".jsx", ".tsx"):
		defs = js_functions(text)
		classes = JS_CLASS.findall(text)
		imports = len(JS_IMPORT.findall(text))
	elif ext in (".c", ".cpp", ".h", ".hpp", ".java", ".cs"):
		defs = C_FUNC.findall(text)
		imports = len(C_INCLUDE.findall(text))

	todos = TODO_RE.findall(text)
	long_defs: list[str] = []
	markers = sorted(m.start() for m in re.finditer(r"^\s*(?:async\s+)?(?:def|function|class)\s+[A-Za-z_$]", text, re.M))
	for i, start in enumerate(markers):
		end = markers[i + 1] if i + 1 < len(markers) else len(text)
		if text[start:end].count("\n") > 60:
			name = re.search(r"(?:def|function|class)\s+([A-Za-z_$][\w$]*)", text[start:start + 120])
			if name:
				long_defs.append(name.group(1))

	avg_line = (sum(len(ln) for ln in non_empty) / len(non_empty)) if non_empty else 0.0

	issues: list[str] = []
	fixes: list[str] = []
	if line_count and not defs and ext == ".py":
		issues.append("No functions or classes — everything runs at import time.")
		fixes.append('Wrap the top-level logic in main() guarded by `if __name__ == "__main__":`.')
	if long_defs:
		issues.append(f"Very long blocks: {', '.join(long_defs[:3])} exceed ~60 lines.")
		fixes.append(f"Split {long_defs[0]} into smaller single-responsibility helpers.")
	if ext == ".py" and defs and not DOCSTRING_RE.search(text):
		issues.append("No module or function docstrings found.")
		fixes.append("Add a short module docstring plus one-line docstrings for public functions.")
	if todos:
		issues.append(f"{len(todos)} unfinished marker(s): {', '.join(sorted(set(todos)))}.")
		fixes.append("Resolve or ticket the outstanding TODO/FIXME items.")
	if avg_line > 110:
		issues.append(f"Long lines (average {avg_line:.0f} characters) hurt readability.")
		fixes.append("Break long lines at around 100 characters.")
	if imports > 25:
		issues.append(f"{imports} import statements — heavy dependency surface.")
		fixes.append("Group or remove unused imports so dependencies are obvious.")
	broad = len(re.findall(r"except\s*:", text)) + len(re.findall(r"except\s+Exception\s*:", text))
	if ext == ".py" and broad:
		issues.append(f"{broad} broad exception handler(s) can silently hide failures.")
		fixes.append("Catch specific exceptions and log the reason instead of swallowing errors.")
	if not fixes:
		fixes.append("No structural problems detected — add tests to lock in current behaviour.")

	stats = {
		"lines": line_count,
		"code_lines": len(non_empty),
		"size_bytes": path.stat().st_size,
		"functions": len(defs),
		"classes": len(classes),
		"imports": imports,
		"todos": len(todos),
		"avg_line_length": round(avg_line, 1),
	}
	stats_line = (
		f"{line_count} lines ({len(non_empty)} non-empty), {len(defs)} functions, "
		f"{len(classes)} classes, {imports} imports, {len(todos)} TODO markers"
	)

	parts = [f"🔎 **Analysis: `{path.name}`**", f"*{purpose}*", "", f"**Metrics** — {stats_line}"]
	if issues:
		parts += ["", "**Findings**"] + [f"- {i}" for i in issues[:6]]
	parts += ["", "**Suggested changes**"] + [f"- {f}" for f in fixes[:5]]

	spoken = f"{path.name}. {purpose} It has {stats_line}. Main suggestion: {fixes[0]}"

	return {
		"ok": True,
		"path": str(path),
		"filename": path.name,
		"purpose": purpose,
		"stats": stats,
		"issues": issues,
		"fixes": fixes,
		"display": "\n".join(parts),
		"spoken": spoken,
		"content": text,
	}


EXTRACT_KINDS = (
	"function", "functions", "def", "defs", "class", "classes", "import", "imports",
	"url", "urls", "link", "links", "email", "emails", "heading", "headings",
	"todo", "todos", "fixme", "number", "numbers", "string", "strings",
	"path", "paths", "column", "columns", "comment", "comments", "constant", "constants",
)


def extract_from_file(target: str | Path, kind: str) -> dict[str, Any]:
	"""Extract structured elements (functions, imports, URLs…) from a file."""
	from tools.smart_files import find_file

	if isinstance(target, Path):
		path = target
	else:
		path = find_file(str(target)) or Path(str(target)).expanduser()
	if path is None or not path.is_file():
		return {"ok": False, "spoken": f"I couldn't locate '{target}' to extract from."}

	text = _read_text(path)
	if text is None:
		return {"ok": False, "spoken": f"{path.name} is a binary file, so I can't extract from it."}

	ext = path.suffix.lower()
	k = (kind or "").lower().strip()

	def _named(seq: list[str]) -> list[str]:
		seen: set[str] = set()
		out: list[str] = []
		for item in seq:
			item = (item or "").strip()
			if item and item not in seen:
				seen.add(item)
				out.append(item)
		return out

	items: list[str] = []
	if k in ("function", "functions", "def", "defs"):
		if ext == ".py":
			items = _named(PY_DEF.findall(text))
		elif ext in (".js", ".ts", ".jsx", ".tsx"):
			items = _named(js_functions(text))
		else:
			items = _named(C_FUNC.findall(text))
		label = "functions"
	elif k in ("class", "classes"):
		items = _named(PY_CLASS.findall(text) if ext == ".py" else JS_CLASS.findall(text))
		label = "classes"
	elif k in ("import", "imports"):
		if ext == ".py":
			items = _named(m.group(0).strip() for m in PY_IMPORT.finditer(text))
		elif ext in (".js", ".ts", ".jsx", ".tsx"):
			items = _named(m.group(0).strip() for m in JS_IMPORT.finditer(text))
		else:
			items = _named(m.group(0).strip() for m in C_INCLUDE.finditer(text))
		label = "imports"
	elif k in ("url", "urls", "link", "links"):
		items = _named(URL_RE.findall(text))
		label = "URLs"
	elif k in ("email", "emails"):
		items = _named(EMAIL_RE.findall(text))
		label = "email addresses"
	elif k in ("heading", "headings"):
		items = _named(HEADING_MD.findall(text))
		label = "headings"
	elif k in ("todo", "todos", "fixme"):
		todos = []
		for m in TODO_RE.finditer(text):
			rest = text[m.end():].lstrip(" :-#")
			first = rest.splitlines()[0] if rest.splitlines() else ""
			todos.append(f"{m.group(1)}: {first.strip()[:90]}")
		items = _named(todos)
		label = "TODO/FIXME markers"
	elif k in ("number", "numbers"):
		items = _named(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text))[:60]
		label = "numbers"
	elif k in ("path", "paths"):
		items = _named(re.findall(r"[A-Za-z]:\\[^\s'\"<>]+|[./][\w./-]{3,}", text))[:60]
		label = "paths"
	elif k in ("constant", "constants"):
		items = _named(re.findall(r"^[A-Z][A-Z0-9_]{2,}\s*=", text, re.M))
		label = "constants"
	elif k in ("comment", "comments"):
		items = _named(ln.strip() for ln in text.splitlines() if ln.strip().startswith(("#", "//")))[:80]
		label = "comments"
	elif k in ("string", "strings"):
		items = _named(re.findall(r"""['"]([^'"\n]{6,120})['"]""", text))[:60]
		label = "string literals"
	elif k in ("column", "columns"):
		items = [c.strip() for c in (text.splitlines()[0].split(",") if text.strip() else [])]
		label = "columns"
	else:
		return {
			"ok": False,
			"spoken": "I can extract functions, classes, imports, URLs, emails, headings, TODOs, comments, constants or paths — which one do you want?",
		}

	if not items:
		return {
			"ok": True,
			"filename": path.name,
			"kind": label,
			"items": [],
			"display": f"🧩 **Extracted {label} from `{path.name}`**\n\n_None found._",
			"spoken": f"I didn't find any {label} in {path.name}.",
		}

	shown = items[:40]
	display = f"🧩 **Extracted {len(items)} {label} from `{path.name}`**\n\n"
	if all(re.match(r"^[\w.$]+$", i) for i in shown):
		display += "```text\n" + "\n".join(shown) + "\n```"
	else:
		display += "\n".join(f"- `{i}`" for i in shown)
	if len(items) > len(shown):
		display += f"\n\n…and {len(items) - len(shown)} more."

	spoken = f"I found {len(items)} {label} in {path.name}: " + ", ".join(shown[:6]) + ("…" if len(items) > 6 else ".")

	return {
		"ok": True,
		"filename": path.name,
		"path": str(path),
		"kind": label,
		"count": len(items),
		"items": items,
		"display": display,
		"spoken": spoken,
	}
