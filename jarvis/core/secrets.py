"""Secret storage backed by Windows Credential Manager.

Secrets are looked up in this order: Credential Manager (target
``JARVIS/<NAME>``), then process environment variables. Values returned are
registered with the log redactor so they can never appear in log files.
Secrets are never written to config.json, memory or conversation history.
"""

from __future__ import annotations

import logging
import os

from jarvis.core.logging_setup import register_secret

log = logging.getLogger(__name__)
_PREFIX = "JARVIS/"

try:
    import win32cred  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - non-Windows or pywin32 missing
    win32cred = None


def get(name: str) -> str | None:
    value: str | None = None
    if win32cred is not None:
        try:
            cred = win32cred.CredRead(_PREFIX + name, win32cred.CRED_TYPE_GENERIC)
            blob = cred["CredentialBlob"]
            value = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
        except Exception:  # noqa: BLE001 - "not found" is the common case
            value = None
    if not value:
        value = os.environ.get(name) or None
    if value:
        register_secret(value)
    return value


def set(name: str, value: str) -> None:  # noqa: A001 - mirrors keyring API
    if win32cred is None:
        raise RuntimeError("Windows Credential Manager is unavailable (pywin32 missing).")
    register_secret(value)
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": _PREFIX + name,
            "UserName": "jarvis",
            "CredentialBlob": value,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )
    log.info("stored secret %s in Credential Manager", name)


def delete(name: str) -> bool:
    if win32cred is None:
        return False
    try:
        win32cred.CredDelete(_PREFIX + name, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def has(name: str) -> bool:
    return bool(get(name))
