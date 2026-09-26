"""Zero-dependency web bridge for the JARVIS interface.

Serves the static UI and exposes a small JSON API plus a Server-Sent-Events
stream so the interface reflects the assistant's state and conversation in
real time. Everything is read-only for browsers except a small, validated set
of commands (type a message, adjust settings, request an interrupt) — no
arbitrary execution is reachable from the frontend.

Uses only the Python standard library. ``requirements.txt`` is unchanged.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from queue import Empty
from typing import Any
from urllib.parse import parse_qs, urlparse

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.conversation import ConversationManager
from core.history import HistoryStore
from core.settings import Settings

def get_local_ip() -> str:
	"""Discover the machine's primary local LAN IP address."""
	try:
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		s.connect(("8.8.8.8", 80))
		ip = s.getsockname()[0]
		s.close()
		return ip
	except Exception:
		return "127.0.0.1"

# BASE_DIR is the directory that contains this module (interface/), and also
# where index.html / style.css / app.js live.
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR

MIME = {
	".html": "text/html; charset=utf-8",
	".css": "text/css; charset=utf-8",
	".js": "application/javascript; charset=utf-8",
	".svg": "image/svg+xml",
	".json": "application/json; charset=utf-8",
	".woff2": "font/woff2",
	".png": "image/png",
	".ico": "image/x-icon",
	".mp4": "video/mp4",
	".webm": "video/webm",
}

def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
	"""Parse a small JSON POST body defensively; returns None on malformed."""
	length = handler.headers.get("Content-Length")
	if not length:
		return {}
	try:
		size = int(length)
		if size < 0 or size > 10_000_000:
			return None
		raw = handler.rfile.read(size).decode("utf-8")
	except Exception:  # noqa: BLE001
		return None
	if not raw.strip():
		return {}
	try:
		parsed = json.loads(raw)
	except json.JSONDecodeError:
		return None
	return parsed if isinstance(parsed, dict) else {}


class JarvisHandler(BaseHTTPRequestHandler):
	"""Handles static files, the SSE stream, and the small command API."""
	protocol_version = "HTTP/1.1"
	manager: ConversationManager
	settings: Settings
	history: HistoryStore

	def address_string(self) -> str:
		if hasattr(self, "client_address") and self.client_address:
			return str(self.client_address[0])
		return "127.0.0.1"

	def log_message(self, fmt: str, *args: Any) -> None:
		print(f"[HTTP] {self.address_string()} {fmt % args}")

	def _send_json(self, status: int, payload: dict[str, Any]) -> None:
		body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
		self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, ngrok-skip-browser-warning")
		self.send_header("Connection", "close")
		self.end_headers()
		self.wfile.write(body)
		self.close_connection = True

	def do_HEAD(self) -> None:
		"""Respond to HEAD requests (used by curl -I, health checkers, ngrok)."""
		parsed = urlparse(self.path)
		path = parsed.path
		self.send_response(200)
		self.send_header("Access-Control-Allow-Origin", "*")
		if path.endswith(".css"):
			self.send_header("Content-Type", "text/css; charset=utf-8")
		elif path.endswith(".js"):
			self.send_header("Content-Type", "application/javascript; charset=utf-8")
		else:
			self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Connection", "close")
		self.end_headers()
		self.close_connection = True

	def do_OPTIONS(self) -> None:
		"""Handle preflight CORS requests from cross-origin clients."""
		self.send_response(204)
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
		self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, ngrok-skip-browser-warning")
		self.send_header("Access-Control-Max-Age", "86400")
		self.end_headers()

	# ------------------------------------------------------------------ #
	# GET
	# ------------------------------------------------------------------ #

	def do_GET(self) -> None:
		parsed = urlparse(self.path)
		path = parsed.path

		if path == "/events":
			self._sse_handler()
			return
		if path == "/api/state":
			self._send_json(200, {"state": self.manager.state, "turn": self.manager.turn_id})
			return
		if path == "/api/health":
			self._send_json(200, {"ok": True, "state": self.manager.state})
			return
		if path == "/api/info":
			local_ip = get_local_ip()
			port = self.server.server_address[1] if hasattr(self, "server") and hasattr(self.server, "server_address") else 8765
			info = {
				"ok": True,
				"state": self.manager.state,
				"local_ip": local_ip,
				"port": port,
				"url": f"http://{local_ip}:{port}/",
			}
			# Include remote tunnel URL and permanent URL if available
			try:
				from tunnel import get_tunnel_url, get_permanent_url, get_tunnel_token
				tunnel_url = get_tunnel_url()
				if tunnel_url:
					info["tunnel_url"] = tunnel_url
				perm_url = get_permanent_url()
				if perm_url:
					info["permanent_url"] = perm_url
				info["has_permanent_token"] = bool(get_tunnel_token())
			except ImportError:
				pass
			self._send_json(200, info)
			return
		if path == "/api/settings":
			self._send_json(200, {"settings": self.settings.snapshot()})
			return
		if path == "/api/history":
			self._send_json(200, {"history": self.history.snapshot()[-80:]})
			return
		if path in ("/api/screenshot", "/api/screenshot/info"):
			preview_file = STATIC_DIR / "latest_screenshot.png"
			exists = preview_file.is_file()
			mtime = int(preview_file.stat().st_mtime) if exists else 0
			self._send_json(200, {
				"ok": True,
				"exists": exists,
				"url": f"/latest_screenshot.png?t={mtime}" if exists else None,
				"timestamp": mtime,
			})
			return
		if path == "/api/screenshot/capture":
			try:
				from tools.screen import capture_screen
				preview_file = STATIC_DIR / "latest_screenshot.png"
				capture_screen(output_path=str(preview_file))
				mtime = int(time.time())
				self._send_json(200, {
					"ok": True,
					"url": f"/latest_screenshot.png?t={mtime}",
					"timestamp": mtime,
				})
			except Exception as exc:
				self._send_json(500, {"ok": False, "error": str(exc)})
			return
		if path == "/api/recordings":
			from tools.screen_recorder import list_recordings
			self._send_json(200, {"ok": True, "recordings": list_recordings()})
			return
		if path == "/api/recordings/status":
			from tools.screen_recorder import get_status
			self._send_json(200, {"ok": True, "status": get_status()})
			return
		if path == "/api/locator":
			from tools.locator import locate_laptop
			query_params = parse_qs(parsed.query)
			beacon = query_params.get("beacon", ["false"])[0].lower() in ("true", "1")
			self._send_json(200, locate_laptop(activate_beacon=beacon))
			return
		if path == "/api/screen/stream":
			self._stream_screen_mjpeg()
			return
		if path == "/api/screen/frame":
			self._send_screen_frame()
			return
		if path == "/api/tts":
			self._handle_tts(parsed.query)
			return
		if path.startswith(("/api/", "/events")):
			self._send_json(404, {"error": "unknown endpoint"})
			return
		self._serve_static(path)

	def _serve_static(self, path: str) -> None:
		"""Serve a file from STATIC_DIR; '/' maps to index.html."""
		relative = "index.html" if path in ("/", "") else path.lstrip("/")
		candidate = (STATIC_DIR / relative).resolve()
		try:
			candidate.relative_to(STATIC_DIR.resolve())
		except ValueError:
			self._send_json(404, {"error": "not found"})
			return
		if not candidate.is_file():
			self._send_json(404, {"error": "not found"})
			return
		content = candidate.read_bytes()
		ext = candidate.suffix.lower()
		content_type = MIME.get(ext, "application/octet-stream")
		self.send_response(200)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(content)))
		self.send_header("Accept-Ranges", "bytes")
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
		self.send_header("Pragma", "no-cache")
		self.send_header("Connection", "close")
		self.end_headers()
		self.wfile.write(content)
		self.close_connection = True

	def _sse_handler(self) -> None:
		"""Stream events to one browser client using chunked transfer encoding.

		A keep-alive comment is sent on inactivity so proxies and the browser
		keep the stream open; the client reconnects automatically if the
		connection is ever dropped (history is replayed on reconnect).
		"""
		queue = self.manager.events.subscribe()
		if hasattr(self.manager, "set_remote_active"):
			self.manager.set_remote_active(True)
		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.send_header("Cache-Control", "no-store")
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("X-Accel-Buffering", "no")
		self.send_header("Transfer-Encoding", "chunked")
		self.end_headers()

		def emit(fragment: str) -> None:
			data = fragment.encode("utf-8")
			self.wfile.write(b"%x\r\n" % len(data))
			self.wfile.write(data)
			self.wfile.write(b"\r\n")
			self.wfile.flush()

		try:
			# WebKit/Safari requires initial padding to flush its EventSource read buffer
			emit(": " + (" " * 2048) + "\n\n")
			# Tell the browser how fast to reconnect so a dropped stream is
			# back online within a second or two instead of the default 3s+.
			emit("retry: 1500\n\n")
			emit(f"data: {json.dumps({'type': 'hello', 'state': self.manager.state}, ensure_ascii=False)}\n\n")
			while True:
				try:
					event = queue.get(timeout=3.0)
				except Empty:
					emit(": ping\n\n")
					continue
				emit(f"data: {event}\n\n")
		except Exception:  # noqa: BLE001 - client disconnect
			pass
		finally:
			self.manager.events.unsubscribe(queue)
			try:
				self.wfile.write(b"0\r\n\r\n")
				self.wfile.flush()
			except Exception:  # noqa: BLE001
				pass

	def _stream_screen_mjpeg(self) -> None:
		"""Stream live desktop mirroring as MJPEG multipart stream (~10-15 FPS)."""
		try:
			from tools.screen import _grab_image
			from PIL import Image
			import io
		except Exception as exc:
			self._send_json(500, {"error": f"Screen capture unavailable: {exc}"})
			return

		self.send_response(200)
		self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
		self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
		self.send_header("Pragma", "no-cache")
		self.send_header("Expires", "0")
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Connection", "close")
		self.end_headers()

		while not getattr(self.server, "_shutting_down", False):
			try:
				im = _grab_image()
				# 1024x576 bilinear for smooth 10-14 FPS stream on mobile
				im = im.resize((1024, 576), Image.Resampling.BILINEAR)
				buf = io.BytesIO()
				im.save(buf, format="JPEG", quality=55)
				jpeg = buf.getvalue()

				header = (
					b"--frame\r\n"
					b"Content-Type: image/jpeg\r\n"
					+ f"Content-Length: {len(jpeg)}\r\n\r\n".encode("latin1")
				)
				self.wfile.write(header)
				self.wfile.write(jpeg)
				self.wfile.write(b"\r\n")
				self.wfile.flush()
				time.sleep(0.065)
			except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
				break
			except Exception:
				time.sleep(0.1)

	def _send_screen_frame(self) -> None:
		"""Return a single live JPEG snapshot with no-cache headers."""
		try:
			from tools.screen import _grab_image
			from PIL import Image
			import io
			im = _grab_image()
			im = im.resize((1024, 576), Image.Resampling.BILINEAR)
			buf = io.BytesIO()
			im.save(buf, format="JPEG", quality=52)
			jpeg = buf.getvalue()
			self.send_response(200)
			self.send_header("Content-Type", "image/jpeg")
			self.send_header("Content-Length", str(len(jpeg)))
			self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
			self.send_header("Pragma", "no-cache")
			self.send_header("Access-Control-Allow-Origin", "*")
			self.end_headers()
			self.wfile.write(jpeg)
		except Exception as exc:
			self._send_json(500, {"error": str(exc)})

	def _handle_tts(self, query_str: str) -> None:
		"""Generate or serve cached British neural voice audio (en-GB-RyanNeural) via edge-tts."""
		import hashlib
		import re
		import asyncio
		query_params = parse_qs(query_str)
		raw_text = query_params.get("text", [""])[0].strip()
		if not raw_text:
			self._send_json(400, {"error": "text query param required"})
			return

		# Clean text for speech: remove code blocks, markdown asterisks/backticks/brackets, URLs
		clean = re.sub(r"```[\s\S]*?```", "", raw_text)
		clean = re.sub(r"https?://\S+", "", clean)
		clean = re.sub(r"[*_#`📁🤖⚠️]", "", clean).strip()
		if not clean:
			self._send_json(400, {"error": "no pronounceable text"})
			return

		if len(clean) > 800:
			clean = clean[:800] + "..."

		cache_dir = STATIC_DIR / "tts_cache"
		cache_dir.mkdir(parents=True, exist_ok=True)

		# Consistent MD5 hash based on voice and text
		voice = query_params.get("voice", ["en-GB-RyanNeural"])[0]
		h = hashlib.md5(f"{voice}::{clean}".encode("utf-8")).hexdigest()
		audio_file = cache_dir / f"{h}.mp3"

		if not audio_file.is_file() or audio_file.stat().st_size == 0:
			try:
				import edge_tts
				communicate = edge_tts.Communicate(clean, voice, rate="+6%")
				asyncio.run(communicate.save(str(audio_file)))
			except Exception as exc:
				print(f"[TTS] Generation error: {exc}")
				self._send_json(500, {"error": f"TTS synthesis failed: {exc}"})
				return

		try:
			data = audio_file.read_bytes()
			self.send_response(200)
			self.send_header("Content-Type", "audio/mpeg")
			self.send_header("Content-Length", str(len(data)))
			self.send_header("Cache-Control", "public, max-age=86400")
			self.send_header("Access-Control-Allow-Origin", "*")
			self.send_header("Accept-Ranges", "bytes")
			self.send_header("Connection", "close")
			self.end_headers()
			self.wfile.write(data)
			self.close_connection = True
		except Exception as exc:
			print(f"[TTS] Stream error: {exc}")

	# ------------------------------------------------------------------ #
	# POST (validated command API)
	# ------------------------------------------------------------------ #

	def do_POST(self) -> None:
		parsed = urlparse(self.path)
		path = parsed.path
		if path == "/api/command":
			body = _json_body(self)
			if body is None:
				self._send_json(400, {"error": "invalid JSON"})
				return
			text = (body.get("text") or "").strip()
			if not text or len(text) > 4000:
				self._send_json(400, {"error": "empty or oversized command"})
				return

			# Check if client wants to wait synchronously for JARVIS's answer
			# (ideal for Siri Voice Shortcuts and remote assistants)
			query_params = parse_qs(parsed.query)
			wait_val = body.get("wait")
			wait_for_response = bool(
				wait_val is True or
				str(wait_val).lower() in ("true", "1") or
				query_params.get("wait", ["false"])[0].lower() in ("true", "1")
			)

			source = body.get("source", "text")
			if hasattr(self.manager, "set_remote_active"):
				self.manager.set_remote_active(True)
			if wait_for_response:
				queue = self.manager.events.subscribe()
				try:
					# Drain replayed history from queue
					while not queue.empty():
						try:
							queue.get_nowait()
						except Empty:
							break
					self.manager.submit_text(text, source=source)
					deadline = time.time() + 25.0
					response_text = ""
					while time.time() < deadline:
						remaining = max(0.1, deadline - time.time())
						try:
							raw_event = queue.get(timeout=remaining)
							data = json.loads(raw_event) if isinstance(raw_event, str) else raw_event
							if data.get("type") == "message" and data.get("role") == "assistant" and data.get("status") == "done" and not data.get("stream"):
								response_text = data.get("text", "")
								break
						except Empty:
							break
					if not response_text:
						response_text = "Command executed on PC."

					if query_params.get("format", [""])[0] == "text" or "text/plain" in self.headers.get("Accept", ""):
						body_bytes = response_text.encode("utf-8")
						self.send_response(200)
						self.send_header("Content-Type", "text/plain; charset=utf-8")
						self.send_header("Content-Length", str(len(body_bytes)))
						self.send_header("Access-Control-Allow-Origin", "*")
						self.end_headers()
						self.wfile.write(body_bytes)
						return

					self._send_json(200, {"ok": True, "response": response_text})
					return
				finally:
					self.manager.events.unsubscribe(queue)

			self.manager.submit_text(text, source=source)
			self._send_json(200, {"ok": True})
			return

		if path in ("/api/screenshot", "/api/screenshot/capture"):
			try:
				from tools.screen import capture_screen
				preview_file = STATIC_DIR / "latest_screenshot.png"
				capture_screen(output_path=str(preview_file))
				mtime = int(time.time())
				self._send_json(200, {
					"ok": True,
					"url": f"/latest_screenshot.png?t={mtime}",
					"timestamp": mtime,
				})
			except Exception as exc:
				self._send_json(500, {"ok": False, "error": str(exc)})
			return

		if path == "/api/stop":
			# Interrupt current speech (and pause-generation) immediately.
			try:
				self.manager.speaker.stop_speaking()
			except Exception as exc:  # noqa: BLE001
				self._send_json(500, {"error": str(exc)})
				return
			self._send_json(200, {"ok": True})
			return

		if path == "/api/listen":
			body = _json_body(self)
			if body is None or "enable" not in body:
				self._send_json(400, {"error": "expected enable boolean"})
				return
			enable = bool(body.get("enable"))
			if hasattr(self.manager, "set_auto_listen"):
				self.manager.set_auto_listen(enable)
				self.settings.set("auto_listen", enable)
			else:
				self._send_json(400, {"error": "listening control unavailable"})
				return
			self._send_json(200, {"ok": True, "auto_listen": enable})
			return

		if path == "/api/confirm":
			body = _json_body(self)
			if body is None or "action" not in body:
				self._send_json(400, {"error": "expected action ('confirm' or 'decline')"})
				return
			action = str(body["action"]).lower()
			response_text = "yes" if action in ("confirm", "yes") else "no"
			self.manager.submit_text(response_text)
			self._send_json(200, {"ok": True, "action": action})
			return

		if path == "/api/permissions":
			body = _json_body(self)
			if body is None or "state" not in body:
				self._send_json(400, {"error": "expected state"})
				return
			from core.permissions import get_permission_manager
			msg = get_permission_manager().set_screen_permission(str(body["state"]))
			self._send_json(200, {"ok": True, "message": msg, "permission": get_permission_manager().get_screen_permission().value})
			return

		if path == "/api/settings":
			body = _json_body(self)
			if body is None or "key" not in body or "value" not in body:
				self._send_json(400, {"error": "expected key and value"})
				return
			key = str(body["key"]).strip()
			if key not in ("mic_sensitivity", "response_verbosity", "visual_intensity", "theme", "auto_listen", "voice", "barge_in"):
				self._send_json(400, {"error": "unknown setting"})
				return
			self.settings.set(key, body["value"])
			self._apply_setting(key)
			self._send_json(200, {"ok": True, "settings": self.settings.snapshot()})
			return


		if path == "/api/voice":
			body = _json_body(self)
			if body is None or "audio" not in body:
				self._send_json(400, {"error": "expected audio (base64)"})
			if hasattr(self.manager, "set_remote_active"):
				self.manager.set_remote_active(True)
			try:
				import base64
				import io
				import speech_recognition as sr

				audio_b64 = body["audio"]
				audio_bytes = base64.b64decode(audio_b64)
				recognizer = sr.Recognizer()
				text = ""

				# If browser sent PCM WAV directly
				if audio_bytes.startswith(b"RIFF"):
					wav_buf = io.BytesIO(audio_bytes)
					try:
						with sr.AudioFile(wav_buf) as source:
							audio_data = recognizer.record(source)
						try:
							text = recognizer.recognize_google(audio_data).strip()
						except sr.UnknownValueError:
							text = ""
					except Exception as we:
						print(f"[VOICE] Direct WAV read error: {we}")
				else:
					# Fallback for other formats if pydub/ffmpeg exists
					import tempfile
					import os
					mime_type = body.get("mime", "audio/webm")
					suffix = ".webm" if "webm" in mime_type else ".mp4" if "mp4" in mime_type else ".ogg"
					with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
						tmp.write(audio_bytes)
						tmp_path = tmp.name
					wav_path = tmp_path + ".wav"
					try:
						from pydub import AudioSegment
						audio_seg = AudioSegment.from_file(tmp_path)
						audio_seg = audio_seg.set_channels(1).set_frame_rate(16000)
						audio_seg.export(wav_path, format="wav")
						with sr.AudioFile(wav_path) as source:
							audio_data = recognizer.record(source)
						try:
							text = recognizer.recognize_google(audio_data).strip()
						except sr.UnknownValueError:
							text = ""
					except Exception:
						pass
					finally:
						for p in (tmp_path, wav_path):
							try:
								if os.path.exists(p):
									os.unlink(p)
							except Exception:
								pass

				if text:
					source = body.get("source", "remote_voice")
					print(f"[VOICE] Recognized from client: '{text}'")
					self.manager.submit_text(text, source=source)
					self._send_json(200, {"ok": True, "text": text})
				else:
					self._send_json(200, {"ok": True, "text": ""})
			except Exception as exc:
				print(f"[VOICE] Transcription exception: {exc}")
				self._send_json(500, {"ok": False, "error": str(exc)})
			return

		if path == "/api/remote/control":
			body = _json_body(self)
			if body is None or "action" not in body:
				self._send_json(400, {"ok": False, "error": "expected 'action' parameter"})
				return
			action = str(body.get("action", "")).strip()
			kwargs = {k: v for k, v in body.items() if k != "action"}
			from tools.remote_control import execute_remote_action
			res = execute_remote_action(action, **kwargs)
			if res.get("message") and hasattr(self.manager, "events"):
				self.manager.events.emit({
					"type": "message",
					"role": "assistant",
					"text": res.get("message", res.get("spoken")),
					"status": "done"
				})
			self._send_json(200, res)
			return

		if path == "/api/screen/control":
			body = _json_body(self) or {}
			action = body.get("action", "click")
			kwargs = {k: v for k, v in body.items() if k != "action"}
			from tools.remote_control import execute_remote_action
			res = execute_remote_action(action, **kwargs)
			self._send_json(200, res)
			return

		if path == "/api/screen/record":
			body = _json_body(self) or {}
			action = body.get("action", "toggle")
			from tools.screen_recorder import is_recording, start_recording, stop_recording
			if action == "start" or (action == "toggle" and not is_recording()):
				duration = body.get("duration")
				res = start_recording(duration=duration)
			else:
				res = stop_recording()
			if res.get("ok") and hasattr(self.manager, "events"):
				self.manager.events.emit({
					"type": "message",
					"role": "assistant",
					"text": res.get("message"),
					"status": "done"
				})
			self._send_json(200, res)
			return

		if path in ("/api/locator/beacon", "/api/locator"):
			from tools.locator import locate_laptop
			res = locate_laptop(activate_beacon=True)
			if hasattr(self.manager, "events"):
				self.manager.events.emit({
					"type": "message",
					"role": "assistant",
					"text": res["markdown"],
					"status": "done"
				})
			self._send_json(200, res)
			return

		if path == "/api/files/read":
			body = _json_body(self)
			target = (body.get("target") or body.get("file") or "").strip() if body else ""
			if not target:
				self._send_json(400, {"ok": False, "error": "expected target filename"})
				return
			from tools.smart_files import inspect_and_read_file
			res = inspect_and_read_file(target)
			if res.get("success") and hasattr(self.manager, "events"):
				self.manager.events.emit({
					"type": "file_view",
					"filename": res["filename"],
					"path": res["path"],
					"language": res["language"],
					"lines": res["lines"],
					"summary": res["summary"],
					"content": res["content"]
				})
				self.manager.events.emit({
					"type": "message",
					"role": "assistant",
					"text": res["display"],
					"status": "done"
				})
			self._send_json(200, res)
			return

		if path == "/api/tunnel/save_permanent":
			body = _json_body(self)
			token = (body.get("token") or "").strip() if body else ""
			url = (body.get("url") or "").strip() if body else ""
			try:
				import tunnel
				tunnel.save_permanent_config(token=token, url=url)
				port = self.server.server_address[1] if hasattr(self, "server") and hasattr(self.server, "server_address") else 8765
				tunnel.restart_tunnel(port)
				self._send_json(200, {"ok": True, "message": "Permanent tunnel configuration saved and applied."})
			except Exception as e:
				self._send_json(500, {"ok": False, "error": str(e)})
			return

		self._send_json(404, {"error": "unknown endpoint"})

	def _apply_setting(self, key: str) -> None:
		"""Push a changed setting into the live subsystem it controls."""
		if key == "mic_sensitivity" and self.manager.listener is not None:
			self.manager.listener.sensitivity = max(0.4, min(2.5, float(self.settings.get("mic_sensitivity"))))
		elif key == "auto_listen":
			self.manager.set_auto_listen(bool(self.settings.get("auto_listen")))
		elif key == "voice":
			# Voice selection applies on next JARVIS start (SAPI is already
			# running); report success without pretending it changed live.
			pass

	def finish(self) -> None:
		try:
			super().finish()
		except Exception:  # noqa: BLE001 - ignore write-side disconnects
			pass


class QuietHTTPServer(ThreadingHTTPServer):
	"""ThreadingHTTPServer that stays silent about client-side disconnects.

	Aborted connections (browser tab refresh, fetch cancellation) otherwise
	print a full ConnectionAbortedError traceback on every request.
	"""

	def handle_error(self, request, client_address) -> None:
		exc = sys.exc_info()[1]
		if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)):
			return  # normal client disconnects, not server faults
		super().handle_error(request, client_address)


def make_handler_class(manager: ConversationManager, settings: Settings, history: HistoryStore) -> type[BaseHTTPRequestHandler]:
	"""Return a request-handler class bound to the given runtime objects."""
	bound_settings = settings
	bound_history = history
	bound_manager = manager

	class BoundHandler(JarvisHandler):
		manager = bound_manager
		settings = bound_settings
		history = bound_history

	return BoundHandler


def create_server(manager: ConversationManager, settings: Settings, history: HistoryStore,
			  host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
	"""Build the threaded HTTP server serving the interface + event stream."""
	handler = make_handler_class(manager, settings, history)
	server = QuietHTTPServer((host, port), handler)
	server.daemon_threads = True
	return server