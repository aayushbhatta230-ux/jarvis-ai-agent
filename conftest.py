"""Shared pytest configuration.

Puts the project root on ``sys.path`` and provides lightweight stand-ins for
optional, environment-specific dependencies so the unit tests can run on a
bare CI runner as well as a fully provisioned workstation.

Only modules that are *not installed* are stubbed, and only from the curated
list below. Anything in that list which really is present is imported normally,
so a full local environment still exercises the real code paths.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Heavy or environment-specific packages that are genuinely optional.
# Installed -> imported for real. Absent -> replaced with a stub.
OPTIONAL_MODULES = (
    "openjarvis",
    "OpenGL",
    "PySide6",
    "PySide2",
    "winsdk",
    "ddgs",
    "edge_tts",
    "pydub",
    "faster_whisper",
    "cv2",
    "pytesseract",
    "yt_dlp",
    "send2trash",
    "pypdf",
    "docx",
    "pyautogui",
    "pygetwindow",
    "pyperclip",
    "pywinctl",
    "openai",
    "webrtcvad",
)


# Dunders that must genuinely be absent. If these resolved, Python would
# treat a stub as a real class and break ``class X(StubBase)`` resolution.
_BLOCKED_DUNDERS = frozenset({"__mro_entries__", "__bases__", "__base__"})


class _Stub(types.ModuleType):
    """A module whose every attribute access yields another stub.

    Attribute chains such as ``docx.shared.Pt(12)`` resolve to stubs, and
    calling one returns a stub, so importing and lightly touching an optional
    library never explodes during collection.
    """

    def __getattr__(self, name: str):
        if name in _BLOCKED_DUNDERS:
            raise AttributeError(name)
        # Some libraries compare __version__ against a string at import time
        # (pyscreeze does), so hand back a real value rather than a stub.
        if name == "__version__":
            return "0.0.0"
        stub = _Stub(f"{self.__name__}.{name}")
        setattr(self, name, stub)
        return stub

    def __call__(self, *args, **kwargs):
        return _Stub(f"{self.__name__}()")


_STUBBED_ROOTS: set[str] = set()


class _StubFinder:
    """Resolve ``import pkg.sub`` for stubbed root packages."""

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root not in _STUBBED_ROOTS:
            return None
        from importlib.machinery import ModuleSpec

        return ModuleSpec(fullname, _StubLoader(), is_package=True)


class _StubLoader:
    def create_module(self, spec):
        module = _Stub(spec.name)
        module.__path__ = []  # type: ignore[attr-defined]
        return module

    def exec_module(self, module):
        return None


def _install_stub(name: str) -> None:
    stub = _Stub(name)
    # A package needs a __path__ so "import stub.submodule" resolves.
    stub.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = stub
    _STUBBED_ROOTS.add(name)


def _is_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


for _name in OPTIONAL_MODULES:
    if _name not in sys.modules and not _is_installed(_name):
        _install_stub(_name)

if _STUBBED_ROOTS:
    sys.meta_path.append(_StubFinder())
