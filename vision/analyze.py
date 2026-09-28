'''Vision analysis module for JARVIS.

Provides OCR on the current screen and returns structured information
including text strings and bounding boxes.

Two engines are supported, in order of preference:

1. **Tesseract** - invoked as a *direct subprocess* (``tesseract.exe`` with
   TSV output). This deliberately avoids importing ``pytesseract``, which
   pulls in ``pandas``/``pyarrow`` at import time; on machines protected by
   an Application Control policy those native DLLs are blocked, which used
   to make OCR report itself as "unreachable" even though the engine was
   installed. Shelling out is also faster: no pandas/pyarrow import cost.
2. **Windows built-in OCR** (``winsdk``) - best-effort fallback.
'''

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from shutil import which
from typing import TYPE_CHECKING, List, Tuple

from tools.screen import _grab_image  # internal helper to capture Pillow Image

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image

# Common install locations for the Tesseract engine binary on Windows.
_TESSERACT_BIN_CANDIDATES = (
    "C:/Program Files/Tesseract-OCR/tesseract.exe",
    "C:/Program Files (x86)/Tesseract-OCR/tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)

# Whether the Windows built-in OCR engine is reachable (best-effort fallback).
HAS_WINDOWS_OCR = False
try:
    import winsdk.windows.media.ocr as _wocr  # type: ignore  # noqa: F401
    HAS_WINDOWS_OCR = True
except Exception:  # pragma: no cover - optional fallback only  # noqa: BLE001
    HAS_WINDOWS_OCR = False

# Cached engine path so we do not re-scan the filesystem on every capture.
_TESSERACT_CMD: str | None = None
_TESSERACT_PROBED = False


@dataclass(frozen=True)
class VisionBox:
    """Bounding box for a piece of recognized text.

    The coordinates are given as (left, top, width, height) in screen pixel
    space, matching the format returned by ``pytesseract.image_to_data``.
    """

    text: str
    bbox: Tuple[int, int, int, int]


@dataclass(frozen=True)
class VisionResult:
    """Result of a full screen analysis.

    * ``full_text`` – concatenated OCR text of the whole screen.
    * ``boxes`` – list of :class:`VisionBox` objects for each recognised word.
    """

    full_text: str
    boxes: List[VisionBox]


def _find_tesseract() -> str | None:
    """Locate the Tesseract binary once and cache the result."""
    global _TESSERACT_CMD, _TESSERACT_PROBED
    if _TESSERACT_PROBED:
        return _TESSERACT_CMD

    _TESSERACT_PROBED = True
    found = which("tesseract")
    if found:
        _TESSERACT_CMD = found
        return _TESSERACT_CMD
    for candidate in _TESSERACT_BIN_CANDIDATES:
        if candidate and os.path.exists(candidate):
            _TESSERACT_CMD = candidate
            return _TESSERACT_CMD
    _TESSERACT_CMD = None
    return None


def tesseract_available() -> bool:
    """True when the Tesseract *engine* can actually be invoked."""
    return _find_tesseract() is not None


def _configure_tesseract() -> bool:
    """Kept for backwards compatibility with older callers.

    The subprocess engine needs no configuration; this simply reports
    whether the binary was found.
    """
    return _find_tesseract() is not None


def _run_tesseract(img) -> str:
    """Run the Tesseract binary on a Pillow image and return raw TSV text."""
    cmd = _find_tesseract()
    if not cmd:
        raise RuntimeError("Tesseract binary not found")

    buf = io.BytesIO()
    # PNG keeps text crisp; PSM 6 treats the capture as a uniform text block,
    # which is the right model for a full desktop screenshot.
    img.save(buf, format="PNG")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.run(
        [cmd, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        creationflags=creationflags,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"tesseract exited with {proc.returncode}")
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def _parse_tsv(tsv_text: str) -> VisionResult:
    """Turn Tesseract TSV output into grouped lines plus per-word boxes."""
    boxes: List[VisionBox] = []
    lines: List[str] = []
    current_key = None
    current_words: List[str] = []

    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if (row.get("level") or "").strip() != "5":
            continue  # 5 == word level
        word = (row.get("text") or "").strip()
        if not word:
            continue
        try:
            left = int(float(row.get("left") or 0))
            top = int(float(row.get("top") or 0))
            width = int(float(row.get("width") or 0))
            height = int(float(row.get("height") or 0))
            conf = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        if conf < 25:  # discard low-confidence noise
            continue

        key = (
            (row.get("block_num") or "").strip(),
            (row.get("par_num") or "").strip(),
            (row.get("line_num") or "").strip(),
        )
        if current_key is not None and key != current_key:
            joined = " ".join(current_words).strip()
            if joined:
                lines.append(joined)
            current_words = []
        current_key = key

        current_words.append(word)
        boxes.append(VisionBox(text=word, bbox=(left, top, width, height)))

    joined = " ".join(current_words).strip()
    if joined:
        lines.append(joined)

    return VisionResult(full_text="\n".join(lines), boxes=boxes)


def analyze_screen(img: Image.Image | None = None) -> VisionResult:
    """Capture the screen and run OCR, returning text and per-word boxes.

    Prefers Tesseract (run as a subprocess). If the engine binary is missing it
    falls back to the Windows built-in OCR engine. If neither is usable it
    raises a clear ``RuntimeError`` so callers never see fabricated text.
    """
    if img is None:
        img = _grab_image()

    if tesseract_available():
        try:
            return _parse_tsv(_run_tesseract(img))
        except Exception as exc:  # noqa: BLE001
            if not HAS_WINDOWS_OCR:
                raise RuntimeError(f"OCR (Tesseract) failed: {exc}") from exc
            tesseract_error: Exception | None = exc
    else:
        tesseract_error = None

    if HAS_WINDOWS_OCR:
        try:
            return _analyze_windows_ocr(img)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"OCR (Windows) failed: {exc}") from exc

    if tesseract_error is not None:
        raise RuntimeError(f"OCR (Tesseract) failed: {tesseract_error}") from tesseract_error

    raise RuntimeError(
        "OCR engine not found. Install Tesseract "
        "(https://github.com/UB-Mannheim/tesseract/wiki) and restart JARVIS, "
        "or run 'pip install winsdk' for the Windows OCR fallback."
    )


def _analyze_tesseract(img: Image.Image | None = None) -> VisionResult:
    """Backwards-compatible wrapper around the subprocess Tesseract engine."""
    if img is None:
        img = _grab_image()
    return _parse_tsv(_run_tesseract(img))


def _analyze_windows_ocr(img: Image.Image | None = None) -> VisionResult:
    """OCR through the Windows 10+ built-in engine (best-effort fallback)."""
    import asyncio
    from io import BytesIO

    import winsdk.windows.graphics.imaging as wimg
    import winsdk.windows.media.ocr as wocr
    import winsdk.windows.storage.streams as wstreams
    from winsdk.windows.media.ocr import OcrEngine

    async def _inner() -> VisionResult:
        langs = await wocr.OcrEngine.available_recognizer_languages
        language = langs[0] if langs and len(langs) else None
        if language is None:
            raise RuntimeError("Windows OCR has no available language packs.")
        engine = OcrEngine.try_create_from_language(language)
        if engine is None:
            raise RuntimeError("Windows OCR engine could not be created.")
        if img is None:
            img_to_use = _grab_image()
        else:
            img_to_use = img
        raw = BytesIO()
        img_to_use.save(raw, format="PNG")
        raw.seek(0)
        mem = wstreams.InMemoryRandomAccessStream()
        writer = wstreams.DataWriter(mem.get_output_stream())
        writer.write_bytes(raw.getvalue())
        await writer.store_async()
        writer.detach_stream()
        mem.seek(0)
        decoder = await wimg.BitmapDecoder.create_async(mem)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        parts: List[str] = []
        boxes: List[VisionBox] = []
        for idx in range(result.lines.size):
            line = result.lines.get_at(idx)
            for word_idx in range(line.words.size):
                word = line.words.get_at(word_idx)
                text = word.text.strip()
                if not text:
                    continue
                parts.append(text)
                rect = word.bounding_rect
                boxes.append(VisionBox(text=text, bbox=(int(rect.x), int(rect.y), int(rect.width), int(rect.height))))
        return VisionResult(full_text=" ".join(parts), boxes=boxes)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()
