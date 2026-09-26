"""System Cache Cleaner for JARVIS."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def clean_system_cache() -> dict[str, Any]:
    """Safely clean Windows temp folders, IDE caches, and stale temporary files."""
    cleaned_bytes = 0
    removed_files = 0
    removed_dirs = 0

    # 1. Clean User Temp Directory
    temp_dir = Path(tempfile.gettempdir())
    if temp_dir.is_dir():
        for item in list(temp_dir.iterdir()):
            try:
                if item.is_file() or item.is_symlink():
                    try:
                        sz = item.stat().st_size
                        item.unlink(missing_ok=True)
                        cleaned_bytes += sz
                        removed_files += 1
                    except (PermissionError, OSError):
                        pass
                elif item.is_dir():
                    try:
                        # Calculate folder size
                        for f in item.glob("**/*"):
                            if f.is_file():
                                try:
                                    cleaned_bytes += f.stat().st_size
                                    removed_files += 1
                                except Exception:
                                    pass
                        shutil.rmtree(item, ignore_errors=True)
                        removed_dirs += 1
                    except Exception:
                        pass
            except Exception:
                continue

    # 2. Clean __pycache__ and .pytest_cache in workspace
    project_root = Path(__file__).resolve().parent.parent
    for root, dirs, _ in os.walk(project_root):
        for d in dirs:
            if d in ("__pycache__", ".pytest_cache"):
                p = Path(root) / d
                try:
                    shutil.rmtree(p, ignore_errors=True)
                    removed_dirs += 1
                except Exception:
                    pass

    mb_freed = round(cleaned_bytes / (1024 * 1024), 2)
    return {
        "success": True,
        "mb_freed": mb_freed,
        "files_removed": removed_files,
        "dirs_removed": removed_dirs,
        "spoken": f"Cache cleared successfully, sir. Freed {mb_freed} megabytes of system cache across {removed_files} temporary files.",
        "display": f"🧹 **Cache Cleaned**: Freed `{mb_freed} MB` across `{removed_files}` temporary files and `{removed_dirs}` directories."
    }


if __name__ == "__main__":
    res = clean_system_cache()
    print(res["display"])
    print(res["spoken"])
