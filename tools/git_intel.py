"""Git Workspace Intelligence and Contribution Automator for JARVIS.

Provides conversational Git telemetry, commit history inspection, and safe
voice-activated commit and push workflows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_git(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=25,
        )
        return proc.returncode, (proc.stdout or proc.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def get_git_status() -> dict[str, Any]:
    """Retrieve detailed Git status and branch summary."""
    code, branch_out = _run_git(["branch", "--show-current"])
    branch = branch_out if code == 0 and branch_out else "main"

    code, status_out = _run_git(["status", "--porcelain"])
    modified: list[str] = []
    untracked: list[str] = []

    if code == 0 and status_out:
        for line in status_out.splitlines():
            line = line.strip()
            if not line:
                continue
            status_code = line[:2]
            filename = line[2:].strip()
            if status_code.startswith("?") or status_code.endswith("?"):
                untracked.append(filename)
            else:
                modified.append(filename)

    is_clean = len(modified) == 0 and len(untracked) == 0

    if is_clean:
        spoken = f"Your Git repository is on branch {branch} and your working tree is completely clean, sir."
    else:
        parts = []
        if modified:
            parts.append(f"{len(modified)} modified file{'s' if len(modified) > 1 else ''}")
        if untracked:
            parts.append(f"{len(untracked)} untracked file{'s' if len(untracked) > 1 else ''}")
        spoken = f"On branch {branch}, you have {' and '.join(parts)}, sir."

    display = (
        f"🌿 **Git Status** (`{branch}`):\n"
        f"- **Working tree**: {'Clean' if is_clean else 'Changes present'}\n"
        f"- **Modified**: `{len(modified)}`\n"
        f"- **Untracked**: `{len(untracked)}`\n"
    )
    if modified:
        display += f"\n*Modified*: {', '.join(modified[:5])}"
    if untracked:
        display += f"\n*Untracked*: {', '.join(untracked[:5])}"

    return {
        "branch": branch,
        "is_clean": is_clean,
        "modified_count": len(modified),
        "untracked_count": len(untracked),
        "modified_files": modified,
        "untracked_files": untracked,
        "spoken": spoken,
        "display": display,
    }


def get_recent_commits(limit: int = 5) -> list[dict[str, str]]:
    """Retrieve recent commits with hash, date, and subject."""
    code, out = _run_git(["log", f"-n{limit}", "--pretty=format:%h|%ar|%s"])
    if code != 0 or not out:
        return []

    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3:
            commits.append({
                "hash": parts[0],
                "date": parts[1],
                "message": parts[2],
            })
    return commits


def quick_commit_and_push(message: str) -> dict[str, Any]:
    """Stage, commit, and push changes to origin/main and sync gh-pages."""
    clean_msg = message.strip() or "chore: quick update by JARVIS"

    # Stage
    c1, o1 = _run_git(["add", "-A"])
    if c1 != 0:
        return {"ok": False, "spoken": f"Could not stage changes: {o1}"}

    # Commit
    c2, o2 = _run_git(["commit", "-m", clean_msg])
    if c2 != 0 and "nothing to commit" not in o2:
        return {"ok": False, "spoken": f"Commit failed: {o2}"}

    # Push main
    c3, o3 = _run_git(["push", "origin", "main"])
    if c3 != 0:
        return {"ok": False, "spoken": f"Push to main failed: {o3}"}

    # Sync gh-pages
    _run_git(["checkout", "gh-pages"])
    _run_git(["merge", "main", "--no-edit"])
    _run_git(["push", "origin", "gh-pages"])
    _run_git(["checkout", "main"])

    return {
        "ok": True,
        "message": clean_msg,
        "spoken": f"Changes committed with message '{clean_msg}' and pushed to GitHub across main and gh-pages, sir.",
        "display": f"🚀 **Pushed to GitHub**: `{clean_msg}` (synced `main` & `gh-pages`)",
    }
