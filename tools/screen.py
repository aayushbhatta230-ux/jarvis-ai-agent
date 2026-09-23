'''Screen capture and vision capabilities for JARVIS.

Provides screen capture functionality that can be used to understand
what's on the screen, extract text via OCR, and feed visual context
to the AI for reasoning about desktop state.
'''

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import base64
import io
import os

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "memory" / "screenshots"

def _ensure_tesseract_configured() -> bool:
    """Point pytesseract at a discovered Tesseract engine if needed."""
    try:
        import pytesseract as _pt
        if getattr(_pt.pytesseract, "tesseract_cmd", None):
            try:
                _pt.get_tesseract_version()
                return True
            except Exception:
                pass
        from vision.analyze import _configure_tesseract
        return _configure_tesseract()
    except ImportError:
        return False


def _grab_image() -> Image.Image:
    """Capture the primary monitor and return a Pillow Image.

    Supports both active interactive sessions and background services by
    attaching to the active input desktop.
    """
    if not HAS_PIL:
        raise RuntimeError("Screen capture requires 'Pillow'. Install with: pip install Pillow")

    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]

    try:
        hinput = user32.OpenInputDesktop(0, False, 0x01FF)
        if hinput:
            user32.SetThreadDesktop(hinput)
    except Exception:
        pass

    try:
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bm = gdi32.SelectObject(hdc_mem, hbm)

        try:
            res = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)
            if not res:
                raise RuntimeError(f"BitBlt failed: {ctypes.GetLastError()}")

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            buffer = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetDIBits(hdc_mem, hbm, 0, h, buffer, ctypes.byref(bmi), 0)
            return Image.frombuffer("RGBA", (w, h), buffer, "raw", "BGRA", 0, 1).convert("RGB")
        finally:
            gdi32.SelectObject(hdc_mem, old_bm)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
    except Exception:
        if HAS_MSS:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        raise

def capture_screen(output_path: str | None = None, width: int = 1920, height: int = 1080, show: bool = False) -> str:
    """Capture the current screen and save it to disk."""
    if output_path is None:
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(desktop / f"screenshot_{timestamp}.png")

    output_path = os.path.expanduser(output_path)
    img = _grab_image()
    if img.width > width or img.height > height:
        img.thumbnail((width, height), Image.Resampling.LANCZOS)

    img.save(output_path, "PNG")
    # Also save a copy for the web interface preview
    try:
        web_preview = Path(__file__).resolve().parent.parent / "interface" / "latest_screenshot.png"
        img.save(str(web_preview), "PNG")
    except Exception:
        pass

    # Only visibly display the screenshot on the PC screen if explicitly requested
    if show:
        try:
            os.startfile(output_path)
        except Exception:
            pass

    return f"Screenshot saved to your Desktop: {os.path.basename(output_path)}"

def get_screen_base64(width: int = 1920, height: int = 1080) -> str:
    """Capture the screen and return it as a base64‑encoded PNG string.

    Useful for sending image data directly to an AI model without writing to disk.
    """
    img = _grab_image()
    if img.width > width or img.height > height:
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")

def extract_text_from_screen() -> str:
    """Capture the screen and extract text using OCR.

    Returns the extracted text. Useful for reading what's on screen.
    """
    if not HAS_TESSERACT:
        raise RuntimeError("OCR requires 'pytesseract'. Install with: pip install pytesseract")
    _ensure_tesseract_configured()
    img = _grab_image()
    text = pytesseract.image_to_string(img)
    return text.strip() if text.strip() else "(No text detected on screen)"

def describe_screen() -> str:
    """Capture and describe what's on the screen.

    Returns a textual description of the screen contents.
    """
    try:
        text = extract_text_from_screen()
        if text and len(text) > 10:
            return f"Screen contains: {text[:200]}..." if len(text) > 200 else f"Screen contains: {text}"
        return "Screen captured but no text content detected."
    except RuntimeError:
        return "Screen capture not available. Install: pip install mss Pillow pytesseract"
