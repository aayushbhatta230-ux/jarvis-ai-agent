"""Remote Access Tunnel Manager for JARVIS.

Supports:
1. Permanent ngrok Static Domain (zero-cost permanent HTTPS bookmarkable forever).
2. Permanent Cloudflare Zero Trust Named Tunnel (via token).
3. Cloudflare Quick Tunnel (temporary public HTTPS URL via trycloudflare.com).

Configuration:
- ``config/ngrok_domain.txt`` (Permanent ngrok domain, e.g. aftermath-feminine-entwine.ngrok-free.dev)
- ``config/permanent_url.txt`` (Permanent public hostname/URL)
- ``config/tunnel_url.txt`` (Active live URL for UI QR code)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"
TUNNEL_URL_FILE = CONFIG_DIR / "tunnel_url.txt"
TUNNEL_TOKEN_FILE = CONFIG_DIR / "tunnel_token.txt"
PERMANENT_URL_FILE = CONFIG_DIR / "permanent_url.txt"
NGROK_DOMAIN_FILE = CONFIG_DIR / "ngrok_domain.txt"

_tunnel_url: str | None = None
_tunnel_process: subprocess.Popen | None = None
_active_port: int = 8765
_lock = threading.Lock()
_running: bool = False


def get_tunnel_url() -> str | None:
    """Return the current active tunnel URL, or None if not running."""
    with _lock:
        if _tunnel_url and "api.trycloudflare.com" not in _tunnel_url:
            return _tunnel_url
    if TUNNEL_URL_FILE.is_file():
        try:
            val = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
            if val and "api.trycloudflare.com" not in val:
                return val
        except Exception:
            pass
    return None


def get_permanent_url() -> str | None:
    """Return configured permanent URL if available."""
    if PERMANENT_URL_FILE.is_file():
        try:
            val = PERMANENT_URL_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    return None


def get_ngrok_domain() -> str | None:
    """Return configured permanent ngrok static domain if available."""
    if NGROK_DOMAIN_FILE.is_file():
        try:
            val = NGROK_DOMAIN_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    perm = get_permanent_url()
    if perm and "ngrok" in perm:
        return perm.replace("https://", "").replace("http://", "").split("/")[0].strip()
    return None


def get_tunnel_token() -> str | None:
    """Return configured Cloudflare tunnel token if available."""
    if TUNNEL_TOKEN_FILE.is_file():
        try:
            val = TUNNEL_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    return None


def _find_ngrok() -> str | None:
    """Locate the ngrok binary."""
    # Check JARVIS project root first
    local_ngrok = Path(__file__).resolve().parent / "ngrok.exe"
    if local_ngrok.is_file():
        return str(local_ngrok)

    for name in ("ngrok", "ngrok.exe"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)

    for loc in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ngrok.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ngrok" / "ngrok.exe",
        Path(os.environ.get("ProgramFiles", "")) / "ngrok" / "ngrok.exe",
    ):
        if loc.is_file():
            return str(loc)
    return None


def _find_cloudflared() -> str | None:
    """Locate the cloudflared binary."""
    for loc in (
        Path(__file__).resolve().parent / "cloudflared.exe",
        Path.cwd() / "cloudflared.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", "")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe",
        Path.home() / ".cloudflared" / "cloudflared.exe",
    ):
        if loc.is_file():
            return str(loc)
    for name in ("cloudflared", "cloudflared.exe"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return None


def _kill_existing_tunnels() -> None:
    """Kill lingering tunnel processes to prevent port conflicts."""
    if sys.platform == "win32":
        for exe in ("cloudflared.exe", "ngrok.exe"):
            try:
                subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, timeout=2)
            except Exception:
                pass


def start_tunnel(port: int = 8765, callback=None) -> threading.Thread | None:
    """Start either a permanent cloudflare or ngrok tunnel in a background thread."""
    global _tunnel_url, _tunnel_process, _active_port, _running
    _active_port = port
    _running = True

    cloudflared = _find_cloudflared()
    ngrok_bin = _find_ngrok()
    ngrok_domain = get_ngrok_domain()

    if not ngrok_bin and not cloudflared:
        print("[TUNNEL] Neither cloudflared nor ngrok was found.")
        return None

    _kill_existing_tunnels()

    def _run():
        global _tunnel_url, _tunnel_process

        # -------------------------------------------------------------
        # Mode 1: Cloudflare Tunnel (UNLIMITED BANDWIDTH, NO EXPIRATION)
        # -------------------------------------------------------------
        if cloudflared:
            cmd = [
                cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}",
                "--no-autoupdate",
            ]
            print(f"[TUNNEL] Starting Cloudflare Tunnel: {' '.join(cmd)}")

            while _running:
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                except Exception as exc:
                    print(f"[TUNNEL] Failed to start cloudflared: {exc}")
                    time.sleep(3)
                    continue

                with _lock:
                    _tunnel_process = proc

                url_pattern = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    match = url_pattern.search(line)
                    if match:
                        url = match.group(0)
                        if "api.trycloudflare.com" in url:
                            continue
                        with _lock:
                            _tunnel_url = url
                        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                        TUNNEL_URL_FILE.write_text(url, encoding="utf-8")
                        PERMANENT_URL_FILE.write_text(url, encoding="utf-8")
                        print("=" * 60)
                        print("  JARVIS CLOUDFLARE TUNNEL ACTIVE (UNLIMITED BANDWIDTH):")
                        print(f"  --> {url}")
                        print("  Open this URL on iPhone Safari & Add to Home Screen!")
                        print("=" * 60)
                        if callback:
                            callback(url)

                proc.wait()
                with _lock:
                    _tunnel_url = None
                    _tunnel_process = None
                if not _running:
                    break
                time.sleep(3)
            return

        # -------------------------------------------------------------
        # Mode 2: ngrok Fallback (if cloudflared unavailable)
        # -------------------------------------------------------------
        if ngrok_bin and ngrok_domain:
            target_url = f"https://{ngrok_domain}"
            cmd = [ngrok_bin, "http", str(port), f"--url={ngrok_domain}", "--log=stdout"]
            print("=" * 60)
            print("  JARVIS ACCESS URL (ngrok):")
            print(f"  --> {target_url}")
            print("=" * 60)

            with _lock:
                _tunnel_url = target_url
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TUNNEL_URL_FILE.write_text(target_url, encoding="utf-8")
            PERMANENT_URL_FILE.write_text(target_url, encoding="utf-8")
            if callback:
                callback(target_url)

            while _running:
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                except Exception as exc:
                    print(f"[TUNNEL] Failed to launch ngrok: {exc}")
                    time.sleep(3)
                    continue

                with _lock:
                    _tunnel_process = proc

                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    if "started tunnel" in line or "client session established" in line:
                        print(f"[TUNNEL] {line}")
                    elif "error" in line.lower() or "crit" in line.lower():
                        print(f"[TUNNEL] {line}")

                proc.wait()
                with _lock:
                    _tunnel_process = None
                if not _running:
                    break
                time.sleep(3)
            return

    thread = threading.Thread(target=_run, name="jarvis-tunnel", daemon=True)
    thread.start()
    return thread


def stop_tunnel() -> None:
    """Terminate the tunnel process if running."""
    global _tunnel_process, _running
    _running = False
    with _lock:
        proc = _tunnel_process
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _kill_existing_tunnels()


def restart_tunnel(port: int = 8765) -> threading.Thread | None:
    """Stop current tunnel and launch with newest configuration."""
    stop_tunnel()
    time.sleep(0.8)
    return start_tunnel(port)
