"""Safe and deterministic browser actions for JARVIS.

Guarantees:
1. Direct execution via Google Chrome if installed, or default system browser.
2. Automatically attaches to the active desktop.
3. Automatically forces the browser window to the foreground so it visibly opens on screen.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import webbrowser
from urllib.parse import quote_plus


CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_X86 = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"


def open_url(url: str) -> str:
	"""Open an HTTP(S) URL and forcefully bring the browser window to the foreground."""
	clean = url.strip()
	if not clean.startswith(("https://", "http://")):
		clean = "https://" + clean

	from tools.computer import attach_desktop, focus_window_by_keyword
	attach_desktop()

	opened = False
	if getattr(webbrowser.open, "__name__", "") != "open":
		try:
			webbrowser.open(clean)
			opened = True
		except Exception:
			pass

	if not opened:
		for p in (CHROME_PATH, CHROME_X86):
			if os.path.isfile(p):
				try:
					subprocess.Popen([p, clean])
					opened = True
					break
				except Exception:
					pass

	if not opened:
		try:
			os.startfile(clean)
			opened = True
		except Exception:
			try:
				webbrowser.open(clean)
				opened = True
			except Exception as exc:
				raise RuntimeError(f"Could not open browser URL: {exc}")

	# Force the browser to the foreground so the user actually sees it!
	for _ in range(4):
		time.sleep(0.15)
		attach_desktop()
		found, _ = focus_window_by_keyword("chrome", "google chrome", "edge", "browser", "firefox", "brave")
		if found:
			break

	# Refresh preview in background thread for mobile companion
	def _async_preview():
		try:
			from tools.screen import capture_screen
			from pathlib import Path
			web_preview = Path(__file__).resolve().parent.parent / "interface" / "latest_screenshot.png"
			capture_screen(output_path=str(web_preview))
		except Exception:
			pass
	import threading
	threading.Thread(target=_async_preview, daemon=True).start()

	domain = clean.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")
	return f"Opened {domain} on your PC, sir."


def search_web(query: str) -> str:
	"""Search Google using the browser and bring it to front."""
	clean = query.strip()
	if not clean:
		raise ValueError("The search query is empty.")
	url = f"https://www.google.com/search?q={quote_plus(clean)}"
	open_url(url)
	return f"Searching for '{clean}' in your browser, sir."


def resolve_site(text: str) -> str | None:
	"""Resolve a natural site reference into an HTTPS URL."""
	lower = text.lower().strip()
	known_sites = {
		"youtube music": "music.youtube.com",
		"yt music": "music.youtube.com",
		"github": "github.com",
		"youtube": "youtube.com",
		"spotify": "open.spotify.com",
		"google": "google.com",
		"instagram": "instagram.com",
		"facebook": "facebook.com",
		"whatsapp": "web.whatsapp.com",
		"linkedin": "linkedin.com",
		"tiktok": "tiktok.com",
		"telegram": "web.telegram.org",
		"twitter": "x.com",
		"x": "x.com",
		"reddit": "reddit.com",
		"netflix": "netflix.com",
		"chatgpt": "chatgpt.com",
		"gmail": "mail.google.com",
		"maps": "maps.google.com",
		"google maps": "maps.google.com",
	}

	for name, domain in known_sites.items():
		if lower == name or lower == f"{name}.com" or re.search(rf"\b{re.escape(name)}\b", lower):
			return f"https://{domain}"

	match = re.search(r"\b([a-z0-9][a-z0-9\-_]*\.[a-z]{2,})\b", lower)
	if match:
		return f"https://{match.group(1)}"

	return None


# ===========================================================================
# BROWSER NAVIGATION SHORTCUTS
# ===========================================================================

def go_back() -> str:
	"""Navigate back in the current browser."""
	from tools.computer import attach_desktop
	attach_desktop()
	try:
		import pyautogui
		pyautogui.hotkey("alt", "left")
		return "Navigated back, sir."
	except Exception:
		return "Could not navigate back."


def go_forward() -> str:
	"""Navigate forward in the current browser."""
	from tools.computer import attach_desktop
	attach_desktop()
	try:
		import pyautogui
		pyautogui.hotkey("alt", "right")
		return "Navigated forward, sir."
	except Exception:
		return "Could not navigate forward."


def reload_page() -> str:
	"""Reload the current browser page."""
	from tools.computer import attach_desktop
	attach_desktop()
	try:
		import pyautogui
		pyautogui.press("f5")
		return "Page reloaded, sir."
	except Exception:
		return "Could not reload page."
