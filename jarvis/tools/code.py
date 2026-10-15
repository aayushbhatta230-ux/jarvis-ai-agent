"""Coding tools: repository analysis, search, semantic index, git, tests, external agents."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool
from jarvis.tools.shell import _format, run_process

_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
             ".cs", ".rb", ".php", ".swift", ".m", ".scala", ".sql", ".sh", ".ps1", ".html", ".css", ".scss",
             ".vue", ".svelte", ".json", ".toml", ".yaml", ".yml", ".md", ".ini", ".cfg", ".gradle", ".xml"}
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next", "target",
         ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage", ".idea", ".vscode", "data", ".kilo"}


def _repo(ctx: ToolContext, path: str) -> Path:
    p = ctx.app.permissions.check_path(os.path.expandvars(path or "."))
    if not p.is_dir():
        raise ToolError(f"{p} is not a directory")
    return p


def _iter_code(root: Path, max_files: int = 5000) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in _CODE_EXT:
                out.append(Path(dirpath) / fn)
                if len(out) >= max_files:
                    return out
    return out


def detect_project(root: Path) -> dict[str, Any]:
    """Identify languages, package managers and the likely test / build / lint commands."""
    info: dict[str, Any] = {"root": str(root), "languages": {}, "test": None, "build": None, "lint": None,
                            "markers": []}
    for f in _iter_code(root, 3000):
        ext = f.suffix.lower()
        info["languages"][ext] = info["languages"].get(ext, 0) + 1
    info["languages"] = dict(sorted(info["languages"].items(), key=lambda kv: -kv[1])[:8])
    has = lambda n: (root / n).exists()  # noqa: E731
    py = root / ".venv" / "Scripts" / "python.exe"
    python = f'"{py}"' if py.exists() else "python"
    if has("pyproject.toml") or has("setup.py") or has("requirements.txt") or has("pytest.ini"):
        info["markers"].append("python")
        info["test"] = f"{python} -m pytest -q"
        info["lint"] = f"{python} -m ruff check ." if shutil.which("ruff") or py.exists() else None
    if has("package.json"):
        info["markers"].append("node")
        try:
            scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        pm = "pnpm" if has("pnpm-lock.yaml") else "yarn" if has("yarn.lock") else "npm"
        info["package_manager"] = pm
        info["scripts"] = scripts
        if "test" in scripts:
            info["test"] = f"{pm} test"
        if "build" in scripts:
            info["build"] = f"{pm} run build"
        if "lint" in scripts:
            info["lint"] = f"{pm} run lint"
    if has("Cargo.toml"):
        info["markers"].append("rust")
        info["test"], info["build"] = "cargo test", "cargo build"
    if has("go.mod"):
        info["markers"].append("go")
        info["test"], info["build"] = "go test ./...", "go build ./..."
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        info["markers"].append("dotnet")
        info["test"], info["build"] = "dotnet test", "dotnet build"
    info["git"] = has(".git")
    return info


@tool("repo_overview", "Summarise a code repository: languages, structure, key files, test/build commands, "
      "git state. Start every coding task with this.",
      {"path": {"type": "string"}}, required=["path"], group="code", timeout=90)
def repo_overview(ctx: ToolContext, path: str) -> ToolResult:
    from jarvis.tools.files import directory_tree

    root = _repo(ctx, path)
    info = detect_project(root)
    readme = next((root / n for n in ("README.md", "README.rst", "README.txt", "readme.md") if (root / n).exists()),
                  None)
    tree = directory_tree(ctx, str(root), depth=2)
    git = ""
    if info["git"]:
        _, git, _ = run_process(["git", "status", "-sb"], str(root), 20)
        _, log, _ = run_process(["git", "log", "--oneline", "-8"], str(root), 20)
        git = git.strip() + "\nRecent commits:\n" + log.strip()
    parts = [f"Project: {root}", f"Detected: {', '.join(info['markers']) or 'unknown'}; languages {info['languages']}",
             f"Test: {info['test']}  Build: {info['build']}  Lint: {info['lint']}", "", tree]
    if git:
        parts += ["", "Git:", git]
    if readme:
        parts += ["", f"{readme.name} (first 60 lines):", "\n".join(readme.read_text(encoding="utf-8",
                                                                                   errors="replace").splitlines()[:60])]
    ctx.app.memory.note_project(root)
    return ToolResult(True, output="\n".join(parts), data=info)


@tool("search_code", "Search code for a regex/text pattern (ripgrep-style). Returns file:line matches.",
      {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string",
       "description": "Filename filter e.g. *.py"}, "ignore_case": {"type": "boolean"}, "limit": {"type": "integer"}},
      required=["pattern", "path"], group="code", timeout=90)
def search_code(ctx: ToolContext, pattern: str, path: str, glob: str = "", ignore_case: bool = True,
                limit: int = 80) -> str:
    import fnmatch

    root = _repo(ctx, path)
    rg = shutil.which("rg")
    if rg:
        argv = [rg, "-n", "--no-heading", "--max-count", "20", "-S" if ignore_case else "-s", pattern, str(root)]
        if glob:
            argv[1:1] = ["-g", glob]
        _, out, _ = run_process(argv, str(root), 60, ctx)
        lines = out.splitlines()[:limit]
        return "\n".join(lines) or "no matches"
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I if ignore_case else 0)
    hits = []
    for f in _iter_code(root):
        ctx.check()
        if glob and not fnmatch.fnmatch(f.name, glob):
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return "\n".join(hits)
        except OSError:
            continue
    return "\n".join(hits) or "no matches"


# -------------------------------------------------------------- semantic index
def _chunks(text: str, size: int = 60, overlap: int = 10) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        j = min(len(lines), i + size)
        chunk = "\n".join(lines[i:j]).strip()
        if chunk:
            out.append((i + 1, j, chunk))
        if j == len(lines):
            break
        i = j - overlap
    return out


@tool("index_codebase", "Build/refresh the semantic (embedding) index of a repository for meaning-based search.",
      {"path": {"type": "string"}}, required=["path"], group="code", timeout=900)
def index_codebase(ctx: ToolContext, path: str) -> str:
    root = _repo(ctx, path)
    db = ctx.app.db
    repo = str(root)
    known = {r["path"]: r["mtime"] for r in db.query(
        "SELECT path, MAX(mtime) AS mtime FROM code_chunks WHERE repo=? GROUP BY path", (repo,))}
    files = [f for f in _iter_code(root, 4000) if f.stat().st_size < 400_000 and f.suffix not in (".json", ".xml")]
    current = {str(f) for f in files}
    for stale in set(known) - current:
        db.execute("DELETE FROM code_chunks WHERE repo=? AND path=?", (repo, stale))
    todo = [f for f in files if known.get(str(f)) != f.stat().st_mtime]
    added = 0
    batch: list[tuple[Path, int, int, str]] = []

    def flush() -> None:
        nonlocal added
        if not batch:
            return
        model, vecs = ctx.app.router.embed([f"{b[0].name}\n{b[3][:2000]}" for b in batch])
        for (f, s, e, c), v in zip(batch, vecs, strict=False):
            db.execute("INSERT INTO code_chunks(repo, path, start_line, end_line, mtime, content, embedding) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (repo, str(f), s, e, f.stat().st_mtime, c, np.asarray(v, dtype=np.float32).tobytes()))
        added += len(batch)
        batch.clear()

    for n, f in enumerate(todo):
        ctx.check()
        db.execute("DELETE FROM code_chunks WHERE repo=? AND path=?", (repo, str(f)))
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for s, e, c in _chunks(text):
            batch.append((f, s, e, c))
            if len(batch) >= 32:
                flush()
        if n % 25 == 0:
            ctx.progress(f"indexed {n}/{len(todo)} changed files")
    flush()
    total = db.one("SELECT COUNT(*) AS n FROM code_chunks WHERE repo=?", (repo,))["n"]
    return f"index up to date: {len(todo)} files re-indexed ({added} chunks); {total} chunks total for {repo}"


@tool("semantic_code_search", "Find code by meaning (e.g. 'where is the auth token refreshed'). "
      "Requires index_codebase first.",
      {"query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}},
      required=["query", "path"], group="code", timeout=60)
def semantic_code_search(ctx: ToolContext, query: str, path: str, limit: int = 8) -> str:
    root = _repo(ctx, path)
    rows = ctx.app.db.query("SELECT path, start_line, end_line, content, embedding FROM code_chunks WHERE repo=?",
                            (str(root),))
    if not rows:
        raise ToolError("repository is not indexed yet; call index_codebase first")
    _, (qv,) = ctx.app.router.embed([query])
    q = np.asarray(qv, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    scores = mat @ q
    best = np.argsort(-scores)[:limit]
    out = []
    for i in best:
        r = rows[int(i)]
        rel = os.path.relpath(r["path"], root)
        snippet = "\n".join(r["content"].splitlines()[:14])
        out.append(f"## {rel}:{r['start_line']}-{r['end_line']}  (score {scores[i]:.2f})\n{snippet}")
    return "\n\n".join(out)


# ------------------------------------------------------------------------- git
def _git(ctx: ToolContext, root: Path, *args: str, timeout: int = 60) -> ToolResult:
    code, out, to = run_process(["git", *args], str(root), timeout, ctx)
    return _format(code, out, to, limit=20_000)


@tool("git_status", "Show git status, current branch and a diff summary.", {"path": {"type": "string"}},
      required=["path"], group="code")
def git_status(ctx: ToolContext, path: str) -> ToolResult:
    root = _repo(ctx, path)
    st = _git(ctx, root, "status", "-sb")
    stat = _git(ctx, root, "diff", "--stat")
    return ToolResult(st.ok, output=st.output + "\n\nUnstaged diff stat:\n" + stat.output, error=st.error)


@tool("git_diff", "Show the git diff (working tree, or staged with staged=true, or against a ref).",
      {"path": {"type": "string"}, "staged": {"type": "boolean"}, "ref": {"type": "string"},
       "file": {"type": "string"}}, required=["path"], group="code")
def git_diff(ctx: ToolContext, path: str, staged: bool = False, ref: str = "", file: str = "") -> ToolResult:
    root = _repo(ctx, path)
    args = ["diff"] + (["--staged"] if staged else []) + ([ref] if ref else []) + (["--", file] if file else [])
    return _git(ctx, root, *args)


@tool("git_log", "Show recent commits.", {"path": {"type": "string"}, "limit": {"type": "integer"}},
      required=["path"], group="code")
def git_log(ctx: ToolContext, path: str, limit: int = 15) -> ToolResult:
    return _git(ctx, _repo(ctx, path), "log", "--oneline", "--decorate", f"-{max(1, min(limit, 100))}")


@tool("git_checkpoint", "Save a recoverable checkpoint of the current working tree WITHOUT changing it "
      "(git stash create + a named ref). Do this before risky multi-file changes.",
      {"path": {"type": "string"}, "label": {"type": "string"}}, required=["path"], category="write", group="code")
def git_checkpoint(ctx: ToolContext, path: str, label: str = "") -> ToolResult:
    root = _repo(ctx, path)
    res = _git(ctx, root, "stash", "create", f"jarvis checkpoint {label}".strip())
    sha = res.output.replace("exit code 0", "").strip().splitlines()[-1] if res.ok and res.output.strip() else ""
    if not sha or len(sha) < 7:
        head = _git(ctx, root, "rev-parse", "HEAD")
        sha = head.output.replace("exit code 0", "").strip()
        note = "working tree clean; checkpoint points at HEAD"
    else:
        note = "uncommitted changes captured"
    name = f"refs/jarvis/checkpoints/{time.strftime('%Y%m%d-%H%M%S')}"
    upd = _git(ctx, root, "update-ref", name, sha)
    if not upd.ok:
        return upd
    return ToolResult(True, output=f"checkpoint {name} -> {sha[:10]} ({note}). Restore with "
                      f"git_restore_checkpoint.", data={"ref": name, "sha": sha})


@tool("git_list_checkpoints", "List JARVIS git checkpoints.", {"path": {"type": "string"}}, required=["path"],
      group="code")
def git_list_checkpoints(ctx: ToolContext, path: str) -> ToolResult:
    return _git(ctx, _repo(ctx, path), "for-each-ref", "--sort=-refname", "--format=%(refname) %(objectname:short)",
                "refs/jarvis/checkpoints")


@tool("git_restore_checkpoint", "Restore tracked files to a checkpoint (current changes are checkpointed first).",
      {"path": {"type": "string"}, "ref": {"type": "string"}}, required=["path", "ref"], category="destructive",
      group="code")
def git_restore_checkpoint(ctx: ToolContext, path: str, ref: str) -> ToolResult:
    root = _repo(ctx, path)
    git_checkpoint(ctx, path, "before-restore")
    return _git(ctx, root, "checkout", ref, "--", ".")


@tool("git_commit", "Stage files (default: all changes) and commit with a message.",
      {"path": {"type": "string"}, "message": {"type": "string"},
       "files": {"type": "array", "items": {"type": "string"}}}, required=["path", "message"], category="write",
      group="code")
def git_commit(ctx: ToolContext, path: str, message: str, files: list[str] | None = None) -> ToolResult:
    root = _repo(ctx, path)
    add = _git(ctx, root, "add", *(files or ["-A"]))
    if not add.ok:
        return add
    return _git(ctx, root, "commit", "-m", message)


@tool("git_branch", "Create and switch to a new branch, or switch to an existing one.",
      {"path": {"type": "string"}, "name": {"type": "string"}, "create": {"type": "boolean"}},
      required=["path", "name"], category="write", group="code")
def git_branch(ctx: ToolContext, path: str, name: str, create: bool = True) -> ToolResult:
    root = _repo(ctx, path)
    return _git(ctx, root, "switch", *(["-c"] if create else []), name)


@tool("git_push", "Push the current branch to its remote (publishes code externally).",
      {"path": {"type": "string"}}, required=["path"], category="external", group="code")
def git_push(ctx: ToolContext, path: str) -> ToolResult:
    return _git(ctx, _repo(ctx, path), "push", "-u", "origin", "HEAD", timeout=120)


# --------------------------------------------------------------- tests & build
def _run_detected(ctx: ToolContext, root: Path, kind: str, override: str, extra: str, timeout: int) -> ToolResult:
    info = detect_project(root)
    command = override or info.get(kind)
    if not command:
        raise ToolError(f"no {kind} command detected; pass command explicitly")
    if extra:
        command = f"{command} {extra}"
    from jarvis.tools.shell import run_command

    res = run_command(ctx, command, cwd=str(root), timeout=timeout)
    res.data["command"] = command
    if kind == "test":
        out = res.output
        summary = re.findall(r"(\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed))", out)
        res.data["summary"] = ", ".join(summary)
    return res


@tool("run_tests", "Run the project's test suite (auto-detected: pytest, npm test, cargo, go, dotnet). "
      "Pass 'args' to select tests, or 'command' to override.",
      {"path": {"type": "string"}, "args": {"type": "string"}, "command": {"type": "string"},
       "timeout": {"type": "integer"}}, required=["path"], category="execute", group="code", timeout=920)
def run_tests(ctx: ToolContext, path: str, args: str = "", command: str = "", timeout: int = 600) -> ToolResult:
    return _run_detected(ctx, _repo(ctx, path), "test", command, args, timeout)


@tool("run_build", "Run the project's build (auto-detected) or an explicit command.",
      {"path": {"type": "string"}, "command": {"type": "string"}, "timeout": {"type": "integer"}},
      required=["path"], category="execute", group="code", timeout=920)
def run_build(ctx: ToolContext, path: str, command: str = "", timeout: int = 600) -> ToolResult:
    return _run_detected(ctx, _repo(ctx, path), "build", command, "", timeout)


@tool("run_lint", "Run the project's linter (auto-detected) or an explicit command.",
      {"path": {"type": "string"}, "command": {"type": "string"}}, required=["path"], category="execute",
      group="code", timeout=320)
def run_lint(ctx: ToolContext, path: str, command: str = "") -> ToolResult:
    return _run_detected(ctx, _repo(ctx, path), "lint", command, "", 300)


@tool("delegate_to_coding_agent", "Hand a well-specified coding task to the external Claude Code agent "
      "(claude CLI) running in the repository. Use for large multi-file implementations. Returns its report.",
      {"path": {"type": "string"}, "task": {"type": "string"}, "timeout": {"type": "integer"}},
      required=["path", "task"], category="execute", group="code", timeout=1900,
      summarize=lambda a: f"run Claude Code in {a.get('path')}: {a.get('task', '')[:300]}")
def delegate_to_coding_agent(ctx: ToolContext, path: str, task: str, timeout: int = 1200) -> ToolResult:
    if ctx.app.config.get("agent.external_coding_agent", "claude") != "claude":
        raise ToolError("external coding agent is disabled in config (agent.external_coding_agent)")
    exe = shutil.which("claude")
    if not exe:
        raise ToolError("the 'claude' CLI is not installed or not on PATH")
    root = _repo(ctx, path)
    code, out, to = run_process([exe, "-p", task, "--output-format", "json"], str(root),
                                max(60, min(timeout, 1800)), ctx)
    try:
        data = json.loads(out.strip().splitlines()[-1])
        report = data.get("result", out)
        ok = not data.get("is_error", False) and code == 0
    except (json.JSONDecodeError, IndexError):
        report, ok = out, code == 0
    return ToolResult(ok and not to, output=str(report)[:12000],
                      error="" if ok and not to else ("timed out" if to else f"claude exited with {code}"))
