"""Structured logging with secret redaction.

Logs are written as JSON lines to ``data/logs/jarvis.log`` (rotated) and as
readable text to the console. A redaction filter masks anything that looks
like an API key, bearer token or password before it reaches any handler.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import time

from jarvis.core import paths

_SECRET_PATTERNS = [
    re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret|x-api-key)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
]

_known_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Mask this exact value anywhere it appears in future log records."""
    if value and len(value) >= 6:
        _known_secrets.add(value)


def redact(text: str) -> str:
    for secret in _known_secrets:
        text = text.replace(secret, "***")
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", text)
        else:
            text = pat.sub("***", text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "thread": record.threadName,
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = extra
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def setup(level: int = logging.INFO, console: bool = True) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)
    redactor = RedactFilter()

    fh = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "jarvis.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(JsonFormatter())
    fh.addFilter(redactor)
    root.addHandler(fh)

    if console and sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        ch.addFilter(redactor)
        root.addHandler(ch)

    for noisy in ("httpx", "httpcore", "urllib3", "comtypes", "PIL", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
