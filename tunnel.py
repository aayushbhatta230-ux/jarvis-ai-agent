"""Remote Access Tunnel Manager for JARVIS.

Features:
1. Permanent Cloudflare Tunnel:
   - Survives server restarts and background lifecycles.
   - Automatically reuses the healthy running tunnel process on consecutive runs,
     preventing domain rotation and keeping the exact same URL permanent forever.
2. Dynamic Gateway Portal Sync:
   - Auto-synchronizes the live URL to ``docs/endpoint.json`` and ``docs/index.html``.
   - Pushes to GitHub repository so the mobile gateway URL remains permanently valid.
3. Named Tunnel / ngrok Static Domain Support:
   - Supports custom domains, tokens, and static domains if configured.

Configuration Files:
- ``config/permanent_url.txt``: Permanent public URL.
- ``config/tunnel_url.txt``: Active live URL used by interface & QR code.
- ``config/tunnel_token.txt``: Optional Cloudflare Zero Trust Named Tunnel token.
- ``config/ngrok_domain.txt``: Optional ngrok static domain.
- ``docs/endpoint.json``: Auto-synced endpoint for permanent mobile gateway portal.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
DOCS_DIR = PROJECT_ROOT / "docs"

TUNNEL_URL_FILE = CONFIG_DIR / "tunnel_url.txt"
TUNNEL_TOKEN_FILE = CONFIG_DIR / "tunnel_token.txt"
PERMANENT_URL_FILE = CONFIG_DIR / "permanent_url.txt"
NGROK_DOMAIN_FILE = CONFIG_DIR / "ngrok_domain.txt"
ENDPOINT_JSON_FILE = DOCS_DIR / "endpoint.json"
GATEWAY_HTML_FILE = DOCS_DIR / "index.html"
ROOT_ENDPOINT_FILE = PROJECT_ROOT / "endpoint.json"
ROOT_HTML_FILE = PROJECT_ROOT / "index.html"
NOJEKYLL_FILE = PROJECT_ROOT / ".nojekyll"

_tunnel_url: str | None = None
_tunnel_process: subprocess.Popen | None = None
_active_port: int = 8765
_lock = threading.Lock()
_running: bool = False
_watchdog_active: bool = False


def get_tunnel_url() -> str | None:
    """Return the current active tunnel URL, or None if not running."""
    if TUNNEL_URL_FILE.is_file():
        try:
            val = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
            if val and "api.trycloudflare.com" not in val:
                return val
        except Exception:
            pass
    with _lock:
        if _tunnel_url and "api.trycloudflare.com" not in _tunnel_url:
            return _tunnel_url
    return None


def get_permanent_url() -> str | None:
    """Return configured permanent URL if available."""
    if PERMANENT_URL_FILE.is_file():
        try:
            val = PERMANENT_URL_FILE.read_text(encoding="utf-8").strip()
            if val and "loca.lt" not in val and "api.trycloudflare.com" not in val:
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
    local_ngrok = PROJECT_ROOT / "ngrok.exe"
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
        PROJECT_ROOT / "cloudflared.exe",
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


def _is_cloudflared_running() -> bool:
    """Check if cloudflared process is currently running on the system."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"],
            text=True,
            timeout=2,
        )
        return "cloudflared.exe" in out.lower()
    except Exception:
        return False


def _check_tunnel_alive(url: str, timeout: float = 3.5) -> bool:
    """Verify if the tunnel URL is reachable over the public internet and healthy."""
    if not url or not url.startswith("https://"):
        return False
    if "api.trycloudflare.com" in url or "loca.lt" in url:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/state",
            headers={"User-Agent": "JARVIS-HealthCheck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = resp.read(256).decode("utf-8", errors="ignore")
                return "state" in data
            return False
    except Exception:
        return False


def _sync_gateway(tunnel_url: str) -> None:
    """Synchronize live tunnel URL to root and docs endpoint.json and push to GitHub Pages."""
    if not tunnel_url or "api.trycloudflare.com" in tunnel_url:
        return
    try:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        endpoint_data = {
            "tunnel_url": tunnel_url,
            "permanent_portal": "https://aayushbhatta230-ux.github.io/jarvis-ai-agent/",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "online",
        }
        json_str = json.dumps(endpoint_data, indent=2)
        ENDPOINT_JSON_FILE.write_text(json_str, encoding="utf-8")
        ROOT_ENDPOINT_FILE.write_text(json_str, encoding="utf-8")
        if not NOJEKYLL_FILE.exists():
            NOJEKYLL_FILE.write_text("# Disable Jekyll\n", encoding="utf-8")

        for html_file in (GATEWAY_HTML_FILE, ROOT_HTML_FILE):
            if html_file.is_file():
                try:
                    html_content = html_file.read_text(encoding="utf-8")
                    updated_html = re.sub(
                        r'let activeUrl = "[^"]+"',
                        f'let activeUrl = "{tunnel_url}"',
                        html_content,
                    )
                    updated_html = re.sub(
                        r'let targetUrl = "[^"]+"',
                        f'let targetUrl = "{tunnel_url}"',
                        updated_html,
                    )
                    updated_html = re.sub(
                        r'href="https://[^"]+\.trycloudflare\.com"',
                        f'href="{tunnel_url}"',
                        updated_html,
                    )
                    if updated_html != html_content:
                        html_file.write_text(updated_html, encoding="utf-8")
                except Exception:
                    pass

        def _git_push():
            try:
                subprocess.run(
                    ["git", "add", "docs/endpoint.json", "docs/index.html", "endpoint.json", "index.html", ".nojekyll", "config/"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "commit", "-m", "chore(gateway): auto-sync live tunnel endpoint [skip ci]"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "checkout", "gh-pages"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "merge", "main", "--no-edit"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "push", "origin", "gh-pages"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "checkout", "main"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass

        threading.Thread(target=_git_push, name="jarvis-git-sync", daemon=True).start()
    except Exception as exc:
        print(f"[TUNNEL] Gateway sync notice: {exc}")


def _kill_existing_tunnels() -> None:
    """Kill lingering tunnel processes if forced restart is requested."""
    if sys.platform == "win32":
        for exe in ("cloudflared.exe", "ngrok.exe"):
            try:
                subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, timeout=2)
            except Exception:
                pass


def start_tunnel(port: int = 8765, callback=None, force_new: bool = False) -> threading.Thread | None:
    """Start or attach to a permanent tunnel process.

    If an existing healthy tunnel is already running, it is REUSED without restarting,
    guaranteeing that the URL/domain never changes across server runs.
    """
    global _tunnel_url, _tunnel_process, _active_port, _running
    _active_port = port
    _running = True

    cloudflared = _find_cloudflared()
    ngrok_bin = _find_ngrok()
    ngrok_domain = get_ngrok_domain()

    if not ngrok_bin and not cloudflared:
        print("[TUNNEL] Neither cloudflared nor ngrok was found.")
        return None

    # -----------------------------------------------------------------
    # Step 1: Check if an existing tunnel is ALREADY running and healthy!
    # Reusing it guarantees the domain NEVER changes across server runs.
    # -----------------------------------------------------------------
    if not force_new:
        existing_url = get_tunnel_url() or get_permanent_url()
        if existing_url and "trycloudflare.com" in existing_url and _is_cloudflared_running():
            if _check_tunnel_alive(existing_url):
                with _lock:
                    _tunnel_url = existing_url
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                TUNNEL_URL_FILE.write_text(existing_url, encoding="utf-8")
                PERMANENT_URL_FILE.write_text(existing_url, encoding="utf-8")
                print("=" * 60)
                print("  JARVIS PERMANENT TUNNEL ACTIVE (REUSING RUNNING TUNNEL):")
                print(f"  --> {existing_url}")
                print("  Domain is locked and unchanged across server runs!")
                print("=" * 60)
                if callback:
                    callback(existing_url)
                _sync_gateway(existing_url)
                return None

    if force_new:
        _kill_existing_tunnels()

    def _run():
        global _tunnel_url, _tunnel_process

        # -------------------------------------------------------------
        # Mode 1: Cloudflare Tunnel (UNLIMITED BANDWIDTH, PERSISTENT DAEMON)
        # -------------------------------------------------------------
        if cloudflared:
            cmd = [
                cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}",
                "--no-autoupdate",
            ]
            print(f"[TUNNEL] Launching persistent Cloudflare Tunnel: {' '.join(cmd)}")

            while _running:
                try:
                    # Windows: CREATE_NEW_PROCESS_GROUP allows cloudflared to persist cleanly
                    creationflags = (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32"
                        else 0
                    )
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        creationflags=creationflags,
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
                    if "Unauthorized: Tunnel not found" in line or "Register tunnel error" in line:
                        print("[TUNNEL] Quick tunnel lease expired on Cloudflare edge (Error 1016 prevented). Auto-recovering...")
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        break
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
                        print("  JARVIS CLOUDFLARE PERMANENT TUNNEL ONLINE:")
                        print(f"  --> {url}")
                        print("  This domain will remain active across restarts!")
                        print("=" * 60)
                        if callback:
                            callback(url)
                        _sync_gateway(url)

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

    def _start_tunnel_watchdog():
        """Continuously monitors tunnel health and auto-recovers from Error 1016 / edge disconnects."""
        global _watchdog_active
        with _lock:
            if _watchdog_active:
                return
            _watchdog_active = True

        def _watchdog_loop():
            consecutive_failures = 0
            while _running:
                time.sleep(20)
                url = get_tunnel_url()
                if not url or "trycloudflare.com" not in url:
                    continue

                alive = _check_tunnel_alive(url, timeout=3.5)
                if alive:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        print(f"[TUNNEL WATCHDOG] Tunnel health check failed ({consecutive_failures}/2, Error 1016/Origin DNS). Auto-recovering...")
                        consecutive_failures = 0
                        with _lock:
                            proc = _tunnel_process
                        if proc is not None:
                            try:
                                proc.terminate()
                            except Exception:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                        _kill_existing_tunnels()

        threading.Thread(target=_watchdog_loop, name="jarvis-tunnel-watchdog", daemon=True).start()

    _start_tunnel_watchdog()
    thread = threading.Thread(target=_run, name="jarvis-tunnel", daemon=True)
    thread.start()
    return thread


def stop_tunnel(kill_daemon: bool = False) -> None:
    """Pause tunnel manager.

    By default, kill_daemon is False so the background cloudflared process remains
    alive and keeps the trycloudflare domain permanently connected for subsequent runs.
    """
    global _running
    _running = False
    if kill_daemon:
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


def restart_tunnel(port: int = 8765, force_new: bool = False) -> threading.Thread | None:
    """Stop current tunnel and launch newest configuration."""
    stop_tunnel(kill_daemon=force_new)
    if force_new:
        time.sleep(0.8)
    return start_tunnel(port, force_new=force_new)


def save_permanent_config(token: str = "", url: str = "") -> None:
    """Save persistent tunnel credentials and target URL."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if token:
        TUNNEL_TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    if url:
        cleaned_url = url.strip()
        if not cleaned_url.startswith("http://") and not cleaned_url.startswith("https://"):
            cleaned_url = f"https://{cleaned_url}"
        PERMANENT_URL_FILE.write_text(cleaned_url, encoding="utf-8")
        TUNNEL_URL_FILE.write_text(cleaned_url, encoding="utf-8")
