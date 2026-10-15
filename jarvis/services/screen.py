"""Screen capture, OCR, change detection, observation and vision analysis."""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from jarvis.core import paths
from jarvis.core.events import State

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)

_ERROR_HINT = re.compile(
    r"(?i)\b(error|exception|traceback|failed|failure|fatal|not recognized|access is denied|cannot|"
    r"could not|unable to|crash(ed)?|stopped working|not responding|warning)\b"
)
_tesseract_ready: bool | None = None


def _tesseract(cmd_path: str | None = None) -> bool:
    global _tesseract_ready
    if _tesseract_ready is not None:
        return _tesseract_ready
    try:
        import pytesseract

        candidates = [cmd_path, r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
        for c in candidates:
            if c and Path(c).exists():
                pytesseract.pytesseract.tesseract_cmd = c
                break
        pytesseract.get_tesseract_version()
        _tesseract_ready = True
    except Exception as exc:  # noqa: BLE001
        log.warning("Tesseract OCR unavailable: %s", exc)
        _tesseract_ready = False
    return _tesseract_ready


def grab(region: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Capture the virtual screen (all monitors) or a (left, top, right, bottom) region."""
    import mss

    with getattr(mss, "MSS", mss.mss)() as sct:
        if region:
            left, top, right, bottom = region
            mon = {"left": left, "top": top, "width": max(1, right - left), "height": max(1, bottom - top)}
        else:
            mon = sct.monitors[0]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def to_b64_png(img: Image.Image, max_side: int = 1344) -> str:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def fingerprint(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L").resize((96, 54)), dtype=np.float32) / 255.0


def difference(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.mean(np.abs(a - b)))


@dataclass
class ScreenContext:
    ts: float = 0.0
    window_title: str = ""
    process: str = ""
    text: str = ""
    error_lines: list[str] = field(default_factory=list)

    def summary(self, max_chars: int = 1500) -> str:
        if not self.ts:
            return ""
        age = int(time.time() - self.ts)
        out = f"Active window: {self.window_title} [{self.process}] (observed {age}s ago)"
        if self.error_lines:
            out += "\nPossible errors visible: " + " | ".join(self.error_lines[:5])
        if self.text:
            out += "\nVisible text (OCR excerpt):\n" + self.text[:max_chars]
        return out


class ScreenService:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.context = ScreenContext()
        self._baseline: dict[str, np.ndarray] = {}
        self._baseline_text: dict[str, str] = {}
        self._last_fp: np.ndarray | None = None
        self._alerted: dict[str, float] = {}  # normalised error line -> when we last alerted about it
        self._window_alert_at: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        _tesseract(app.config.get("screen.tesseract_path"))

    # ------------------------------------------------------------ primitives
    @staticmethod
    def ocr(img: Image.Image) -> str:
        if not _tesseract():
            return ""
        import pytesseract

        gray = img.convert("L")
        if gray.width < 1600:
            gray = gray.resize((gray.width * 2, gray.height * 2))
        return pytesseract.image_to_string(gray, config="--psm 3")

    @staticmethod
    def find_text(query: str, region: tuple[int, int, int, int] | None = None) -> dict[str, Any] | None:
        """Locate text on screen with OCR; returns its screen-coordinate centre."""
        if not _tesseract():
            return None
        import pytesseract

        img = grab(region)
        ox, oy = (region[0], region[1]) if region else ScreenService._virtual_origin()
        scale = 2 if img.width < 2000 else 1
        big = img.convert("L").resize((img.width * scale, img.height * scale)) if scale > 1 else img.convert("L")
        data = pytesseract.image_to_data(big, output_type=pytesseract.Output.DICT)
        words = [
            (data["text"][i], data["left"][i], data["top"][i], data["width"][i], data["height"][i], data["line_num"][i],
             data["block_num"][i])
            for i in range(len(data["text"])) if data["text"][i].strip()
        ]
        q = query.lower().split()
        if not q:
            return None
        for i in range(len(words)):
            seq = words[i : i + len(q)]
            if len(seq) < len(q):
                break
            if all(q[j] in seq[j][0].lower() for j in range(len(q))):
                left = min(w[1] for w in seq)
                top = min(w[2] for w in seq)
                right = max(w[1] + w[3] for w in seq)
                bottom = max(w[2] + w[4] for w in seq)
                cx = ox + (left + right) // (2 * scale)
                cy = oy + (top + bottom) // (2 * scale)
                return {"text": " ".join(w[0] for w in seq), "center": [cx, cy]}
        return None

    @staticmethod
    def _virtual_origin() -> tuple[int, int]:
        import mss

        with getattr(mss, "MSS", mss.mss)() as sct:
            m = sct.monitors[0]
            return m["left"], m["top"]

    def active_window_region(self) -> tuple[dict[str, Any] | None, tuple[int, int, int, int] | None]:
        from jarvis.tools.desktop import active_window

        win = active_window()
        if not win:
            return None, None
        left, top, right, bottom = win["rect"]
        if right - left < 50 or bottom - top < 50 or win["minimized"]:
            return win, None
        return win, (left, top, right, bottom)

    def save(self, img: Image.Image, label: str = "screen") -> Path:
        folder = paths.sub("screenshots")
        path = folder / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        img.save(path)
        old = sorted(folder.glob("*.png"))[:-200]
        for p in old:
            p.unlink(missing_ok=True)
        return path

    # ------------------------------------------------------------- context
    def refresh_context(self, force: bool = False) -> ScreenContext:
        win, region = self.active_window_region()
        img = grab(region)
        fp = fingerprint(img)
        threshold = float(self.app.config.get("screen.change_threshold", 0.02))
        same_window = win and win["title"] == self.context.window_title
        if not force and same_window and difference(fp, self._last_fp) < threshold:
            self.context.ts = time.time()
            return self.context
        self._last_fp = fp
        text = self.ocr(img)
        errors = [ln.strip() for ln in text.splitlines() if _ERROR_HINT.search(ln) and len(ln.strip()) > 6][:8]
        prev_errors = set(self.context.error_lines)
        self.context = ScreenContext(time.time(), win["title"] if win else "", win["process"] if win else "",
                                     re.sub(r"\n{3,}", "\n\n", text).strip(), errors)
        self.app.events.publish("screen.context", window=self.context.window_title, process=self.context.process,
                                errors=len(errors))
        new_errors = [e for e in errors if e not in prev_errors]
        if new_errors and self.app.config.get("screen.observe") == "continuous":
            new_errors = self._worth_alerting(win, new_errors)
            if new_errors:
                self.app.events.publish("screen.alert", window=self.context.window_title, lines=new_errors[:3])
        return self.context

    def _worth_alerting(self, win: dict[str, Any] | None, lines: list[str]) -> list[str]:
        """Filter error lines down to ones the user hasn't already been told about.

        Without this, JARVIS reads its own window (full of words like "failed") and its own
        "Possible error" notifications, and OCR jitter makes the same line look new every scan.
        """
        if win and win.get("pid") == os.getpid():
            return []
        now = time.time()
        title = win["title"] if win else ""
        if now - self._window_alert_at.get(title, 0.0) < 60:  # at most one alert per window per minute
            return []
        self._alerted = {k: t for k, t in self._alerted.items() if now - t < 600}
        fresh = []
        for ln in lines:
            if "possible error" in ln.lower():  # our own notification showing on screen
                continue
            key = re.sub(r"[^a-z]+", "", ln.lower())  # ignore digits/punctuation that OCR and clocks change
            if key and key not in self._alerted:
                self._alerted[key] = now
                fresh.append(ln)
        if fresh:
            self._window_alert_at[title] = now
        return fresh

    def analyze(self, question: str, region: str = "screen", cancel: Any = None) -> dict[str, Any]:
        """Answer a question about the screen using a vision model plus OCR."""
        self.app.events.state(State.SCREEN, "analyzing the screen")
        win, wregion = self.active_window_region()
        img = grab(wregion if region == "window" and wregion else None)
        text = self.ocr(img)
        path = self.save(img, "analysis")
        prompt = (
            f"Question about the user's screen: {question}\n"
            f"Active window: {win['title'] if win else '?'} [{win['process'] if win else '?'}]\n"
            "OCR text (may contain recognition errors):\n" + text[:6000] + "\n\n"
            "Answer from what is actually visible. Name specific apps, buttons, messages and values. "
            "If you see an error, quote it and explain likely causes and the fix. Do not invent details."
        )
        from jarvis.providers.router import NoModelAvailable

        try:
            result = self.app.router.chat("vision", [
                {"role": "system", "content": "You are JARVIS's vision module. Be precise and factual."},
                {"role": "user", "content": prompt, "images": [to_b64_png(img)]},
            ], cancel=cancel, max_tokens=900, temperature=0.2)
            answer, method = result.text, f"vision:{result.provider}:{result.model}"
        except NoModelAvailable as exc:
            log.warning("vision unavailable, OCR-only analysis: %s", exc)
            result = self.app.router.chat("chat", [
                {"role": "system", "content": "You only have OCR text of the screen, not the image. Say so if "
                                              "the question needs visual details."},
                {"role": "user", "content": prompt},
            ], cancel=cancel, max_tokens=700, temperature=0.2)
            answer, method = result.text, f"ocr+text:{result.provider}:{result.model}"
        return {"answer": answer, "method": method, "screenshot": str(path),
                "window": win["title"] if win else "", "ocr_chars": len(text)}

    # ----------------------------------------------------- before/after diff
    def baseline(self, label: str = "default") -> str:
        img = grab()
        self._baseline[label] = fingerprint(img)
        self._baseline_text[label] = self.ocr(img)
        return label

    def compare(self, label: str = "default") -> dict[str, Any]:
        if label not in self._baseline:
            raise KeyError(f"no baseline '{label}'")
        img = grab()
        diff = difference(self._baseline[label], fingerprint(img))
        now = self.ocr(img)
        before_lines = set(self._baseline_text[label].splitlines())
        new_lines = [ln for ln in now.splitlines() if ln.strip() and ln not in before_lines][:40]
        return {"visual_change": round(diff, 4), "changed": diff > 0.01, "new_text_lines": new_lines}

    # ------------------------------------------------------------ observer
    def start_observer(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._observe, name="jarvis-screen-observer", daemon=True)
        self._thread.start()

    def stop_observer(self) -> None:
        self._stop.set()

    # --------------------------------------------------------- live sharing
    @property
    def sharing(self) -> bool:
        return bool(self.app.config.get("screen.sharing", False))

    def set_sharing(self, on: bool) -> None:
        """Live screen sharing: JARVIS keeps watching the screen and answers questions about it instantly."""
        self.app.config.set("screen.sharing", on)
        if on:
            self.start_observer()
        self.app.events.publish("screen.sharing", on=on)
        log.info("screen sharing %s", "on" if on else "off")

    def latest_frame(self, max_age: float = 3.0) -> tuple[Image.Image, str]:
        """Most recent full-screen frame (captured now if the cached one is stale) and its OCR text."""
        frame = getattr(self, "_frame", None)
        if frame is None or time.time() - frame[0] > max_age:
            img = grab()
            text = self.ocr(img)
            self._frame = (time.time(), img, text)
            frame = self._frame
        return frame[1], frame[2]

    def _share_tick(self) -> None:
        img = grab()
        fp = fingerprint(img)
        changed = difference(fp, getattr(self, "_share_fp", None)) >= float(
            self.app.config.get("screen.change_threshold", 0.02))
        if not changed and getattr(self, "_frame", None):
            self._frame = (time.time(), self._frame[1], self._frame[2])
            return
        self._share_fp = fp
        text = self.ocr(img)  # OCR only when the picture actually changed
        self._frame = (time.time(), img, text)
        preview = img.copy()
        preview.thumbnail((480, 300))
        buf = io.BytesIO()
        preview.save(buf, format="JPEG", quality=70)
        self.app.events.publish("screen.frame", jpeg=buf.getvalue(), width=img.width, height=img.height)
        self.refresh_context(force=False)

    def _observe(self) -> None:
        while not self._stop.is_set():
            mode = self.app.config.get("screen.observe", "on_demand")
            interval = max(1.5, float(self.app.config.get("screen.interval_seconds", 4.0)))
            try:
                if self.sharing:
                    self._share_tick()
                    interval = 1.5
                elif mode == "continuous":
                    self.refresh_context()
            except Exception:  # noqa: BLE001
                log.exception("screen observation failed")
                self._stop.wait(10)
            self._stop.wait(interval)
