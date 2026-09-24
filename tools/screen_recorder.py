"""
High-Performance Screen Recorder for JARVIS
=============================================
Records active desktop display using mss + OpenCV (cv2) VideoWriter.
Saves to user's Videos/JARVIS_Recordings directory and exposes videos
for web companion playback and downloading.
"""

from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Directories
USER_VIDEOS = Path(os.path.expanduser("~")) / "Videos" / "JARVIS_Recordings"
USER_VIDEOS.mkdir(parents=True, exist_ok=True)

INTERFACE_REC_DIR = Path(__file__).resolve().parent.parent / "interface" / "recordings"
INTERFACE_REC_DIR.mkdir(parents=True, exist_ok=True)

_recorder_lock = threading.Lock()
_recording_thread: threading.Thread | None = None
_is_recording: bool = False
_stop_signal = threading.Event()
_start_time: float = 0.0
_current_file: Path | None = None
_frames_captured: int = 0
_last_completed_file: Path | None = None


def is_recording() -> bool:
    """Return True if a screen recording is currently active."""
    return _is_recording


def get_status() -> dict[str, Any]:
    """Get current recording status and elapsed time."""
    with _recorder_lock:
        elapsed = (time.time() - _start_time) if _is_recording else 0.0
        return {
            "recording": _is_recording,
            "elapsed_seconds": round(elapsed, 1),
            "frames": _frames_captured,
            "current_file": _current_file.name if _current_file else None,
            "last_file": _last_completed_file.name if _last_completed_file else None,
            "last_url": f"/recordings/{_last_completed_file.name}" if _last_completed_file else None,
        }


def start_recording(duration: float | None = None, fps: float = 20.0, output_name: str | None = None) -> dict[str, Any]:
    """Start screen recording in a background worker thread.

    Args:
        duration: Optional maximum duration in seconds. If provided, auto-stops.
        fps: Target frame rate (default 20.0).
        output_name: Optional custom filename without extension.
    """
    global _recording_thread, _is_recording, _stop_signal, _start_time, _current_file, _frames_captured

    if not HAS_CV2 or not HAS_MSS:
        return {
            "ok": False,
            "error": "OpenCV (cv2) or mss is missing. Install with: pip install opencv-python mss",
        }

    with _recorder_lock:
        if _is_recording:
            elapsed = round(time.time() - _start_time, 1)
            return {
                "ok": False,
                "error": f"Recording already in progress ({elapsed}s elapsed).",
                "file": _current_file.name if _current_file else None,
            }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = output_name.strip() if output_name else f"recording_{timestamp}"
        filename = f"{base_name}.mp4"
        _current_file = USER_VIDEOS / filename
        _is_recording = True
        _stop_signal.clear()
        _start_time = time.time()
        _frames_captured = 0

    def _worker():
        global _is_recording, _frames_captured, _last_completed_file
        target_path = _current_file
        web_target = INTERFACE_REC_DIR / target_path.name
        
        try:
            # Ensure worker thread is attached to the interactive desktop
            import ctypes
            try:
                user32 = ctypes.windll.user32
                hinput = user32.OpenInputDesktop(0, False, 0x01FF)
                if hinput:
                    user32.SetThreadDesktop(hinput)
            except Exception:
                pass

            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                width = monitor["width"]
                height = monitor["height"]
                
                # Use mp4v codec for clean MP4 container
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(target_path), fourcc, fps, (width, height))
                
                frame_interval = 1.0 / fps
                next_frame_time = time.time()

                while not _stop_signal.is_set():
                    now = time.time()
                    if duration and (now - _start_time) >= duration:
                        break

                    # Capture frame
                    raw = sct.grab(monitor)
                    # Convert BGRA to BGR
                    frame = np.array(raw)
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    
                    writer.write(frame_bgr)
                    _frames_captured += 1

                    next_frame_time += frame_interval
                    sleep_time = next_frame_time - time.time()
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    else:
                        next_frame_time = time.time()

                writer.release()
                
            # Copy to web recordings directory for immediate streaming & mobile download
            try:
                import shutil
                shutil.copy2(str(target_path), str(web_target))
            except Exception:
                pass

            _last_completed_file = target_path
            print(f"[RECORDER] Screen recording saved to: {target_path} ({_frames_captured} frames)")
        except Exception as exc:
            print(f"[RECORDER] Error during screen recording: {exc}")
        finally:
            with _recorder_lock:
                _is_recording = False

    _recording_thread = threading.Thread(target=_worker, name="jarvis-screen-recorder", daemon=True)
    _recording_thread.start()

    return {
        "ok": True,
        "message": f"Screen recording started ({fps} fps).",
        "file": _current_file.name,
        "duration_limit": duration,
    }


def stop_recording() -> dict[str, Any]:
    """Stop active screen recording and finalize output file."""
    global _stop_signal, _is_recording, _last_completed_file

    with _recorder_lock:
        if not _is_recording:
            return {"ok": False, "error": "No active recording to stop."}
        _stop_signal.set()

    # Wait for thread to finalize file
    if _recording_thread:
        _recording_thread.join(timeout=4.0)

    elapsed = round(time.time() - _start_time, 1)
    file_path = _last_completed_file or _current_file
    web_url = f"/recordings/{file_path.name}" if file_path else None

    return {
        "ok": True,
        "message": f"Screen recording completed ({elapsed}s, {_frames_captured} frames).",
        "file": file_path.name if file_path else "recording.mp4",
        "path": str(file_path) if file_path else "",
        "url": web_url,
        "elapsed_seconds": elapsed,
        "frames": _frames_captured,
    }


def list_recordings() -> list[dict[str, Any]]:
    """List all available screen recordings."""
    results = []
    if USER_VIDEOS.is_dir():
        for p in sorted(USER_VIDEOS.glob("*.mp4"), key=os.path.getmtime, reverse=True):
            stat = p.stat()
            results.append({
                "filename": p.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"/recordings/{p.name}",
            })
    return results
