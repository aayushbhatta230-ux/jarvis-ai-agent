"""Email and Instagram messaging.

Sending is an *external* action: the permission manager shows/speaks the full
message and waits for the user's "yes" before anything leaves the computer.
After sending, each tool verifies the result (SMTP acceptance + Sent folder for
email, the message visible in the conversation for Instagram) and only then
reports success.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import re
import smtplib
import ssl
import time
import webbrowser
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from jarvis.core import secrets
from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# ------------------------------------------------------------------ helpers
def _creds() -> tuple[str, str]:
    addr = secrets.get("JARVIS_GMAIL_ADDRESS")
    pw = secrets.get("JARVIS_GMAIL_APP_PASSWORD")
    if not addr or not pw:
        raise ToolError("email isn't set up: add your Gmail address and an app password in Settings → AI models "
                        "(create the app password at myaccount.google.com/apppasswords)")
    return addr, pw.replace(" ", "")


def resolve_recipient(ctx: ToolContext, to: str) -> str:
    """Accept an address, or look a name up in memory contacts."""
    found = _EMAIL_RE.findall(to)
    if found:
        return ", ".join(found)
    hits = ctx.app.memory.search(f"contact {to} email", k=5, kinds=["contact"], min_score=0.0, semantic=False)
    for h in hits:
        if to.lower() in h["content"].lower():
            m = _EMAIL_RE.search(h["content"])
            if m:
                return m.group(0)
    raise ToolError(f"I don't have an email address for '{to}'. Tell me their address (I'll remember it).")


def _summ_email(a: dict[str, Any]) -> str:
    return (f"send an email to {a.get('to')} — subject “{a.get('subject', '')}”:\n{a.get('body', '')[:600]}")


@tool("send_email", "Send an email from the user's Gmail. Compose a complete, natural message first (greeting, body, "
      "sign-off with the user's name). The user is shown the full email and must approve before it is sent; the "
      "result confirms delivery to Gmail.",
      {"to": {"type": "string", "description": "Email address(es) or a saved contact name"},
       "subject": {"type": "string"}, "body": {"type": "string"},
       "cc": {"type": "string"}},
      required=["to", "subject", "body"], category="external", group="comms", timeout=90, summarize=_summ_email)
def send_email(ctx: ToolContext, to: str, subject: str, body: str, cc: str = "") -> ToolResult:
    addr, pw = _creds()
    recipients = resolve_recipient(ctx, to)
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = recipients
    if cc:
        msg["Cc"] = resolve_recipient(ctx, cc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg_id = email.utils.make_msgid(domain=addr.split("@")[-1])
    msg["Message-ID"] = msg_id
    msg.set_content(body)
    ctx.check()
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(addr, pw)
            refused = server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise ToolError("Gmail rejected the login - check the app password in Settings") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise ToolError(f"sending failed: {exc.__class__.__name__}: {exc}") from exc
    if refused:
        raise ToolError(f"Gmail refused these recipients: {', '.join(refused)}")
    in_sent = _verify_sent(addr, pw, msg_id)
    for name_part in ([to] if not _EMAIL_RE.search(to) else []):
        ctx.app.memory.add(f"Contact '{name_part}': {recipients}", kind="contact", importance=0.6, source="email")
    return ToolResult(True, output=f"Email sent to {recipients} at {time.strftime('%H:%M')} — Gmail accepted it"
                      + (" and it's in your Sent folder." if in_sent else "."),
                      data={"message_id": msg_id, "verified_in_sent": in_sent})


def _verify_sent(addr: str, pw: str, msg_id: str) -> bool:
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", timeout=15) as imap:
            imap.login(addr, pw)
            for box in ('"[Gmail]/Sent Mail"', '"[Google Mail]/Sent Mail"', "Sent"):
                if imap.select(box, readonly=True)[0] == "OK":
                    for _ in range(3):
                        typ, data = imap.search(None, "HEADER", "Message-ID", msg_id)
                        if typ == "OK" and data and data[0].split():
                            return True
                        time.sleep(1.5)
                    return False
    except Exception:  # noqa: BLE001 - verification is best effort
        return False
    return False


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


@tool("read_emails", "Read recent emails from the user's Gmail inbox (sender, subject, date, short preview).",
      {"limit": {"type": "integer"}, "unread_only": {"type": "boolean"},
       "search": {"type": "string", "description": "Optional text to search for (from/subject/body)"}},
      group="comms", timeout=60)
def read_emails(ctx: ToolContext, limit: int = 5, unread_only: bool = False, search: str = "") -> ToolResult:
    from jarvis.tools.research import untrusted

    addr, pw = _creds()
    out = []
    with imaplib.IMAP4_SSL("imap.gmail.com", timeout=20) as imap:
        imap.login(addr, pw)
        imap.select("INBOX", readonly=True)
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        if search:
            criteria = ["TEXT", f'"{search}"'] + (["UNSEEN"] if unread_only else [])
        typ, data = imap.search(None, *criteria)
        ids = data[0].split()[-max(1, min(limit, 20)):] if typ == "OK" and data[0] else []
        for mid in reversed(ids):
            ctx.check()
            typ, msg_data = imap.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            body = ""
            for part in (msg.walk() if msg.is_multipart() else [msg]):
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    payload = part.get_payload(decode=True) or b""
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
            preview = re.sub(r"\s+", " ", body)[:400]
            out.append(f"From: {_decode(msg.get('From'))}\nDate: {msg.get('Date', '')}\n"
                       f"Subject: {_decode(msg.get('Subject'))}\n{preview}")
    if not out:
        return ToolResult(True, output="No matching emails.")
    return ToolResult(True, output=untrusted("gmail inbox", "\n\n---\n\n".join(out)), data={"count": len(out)})


# ---------------------------------------------------------------- Instagram
def _browser_window(title_part: str, timeout: float, ctx: ToolContext) -> dict[str, Any] | None:
    from jarvis.tools.desktop import list_top_windows

    end = time.time() + timeout
    while time.time() < end:
        for w in list_top_windows():
            if title_part.lower() in w["title"].lower() and w["process"].lower() in (
                    "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe"):
                return w
        if ctx.cancel.wait(0.5):
            return None
    return None


def _uia_find(root: Any, predicate: Any, max_depth: int = 40, limit: int = 4000) -> Any:
    """Breadth-first search of the UI Automation tree (web pages are deep)."""
    from collections import deque

    queue = deque([(root, 0)])
    seen = 0
    while queue and seen < limit:
        ctrl, depth = queue.popleft()
        seen += 1
        try:
            if depth and predicate(ctrl):
                return ctrl
            if depth < max_depth:
                for child in ctrl.GetChildren():
                    queue.append((child, depth + 1))
        except Exception:  # noqa: BLE001 - controls can vanish while the page updates
            continue
    return None


def _click(ctrl: Any) -> None:
    import pyautogui

    try:
        ctrl.GetInvokePattern().Invoke()
        return
    except Exception:  # noqa: BLE001
        pass
    r = ctrl.BoundingRectangle
    pyautogui.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)


def _summ_ig(a: dict[str, Any]) -> str:
    return f"send an Instagram message to {a.get('recipient')}:\n“{a.get('message', '')[:600]}”"


@tool("send_instagram_message", "Send an Instagram direct message using the user's logged-in browser: opens the "
      "inbox, finds the person by name, types the message and sends it. Write the full message yourself first (warm, "
      "natural, in the user's voice). The user approves the exact text before sending; the result confirms the "
      "message is visible in the chat.",
      {"recipient": {"type": "string", "description": "Name or username as shown in Instagram"},
       "message": {"type": "string"}},
      required=["recipient", "message"], category="external", group="comms", timeout=150, summarize=_summ_ig)
def send_instagram_message(ctx: ToolContext, recipient: str, message: str) -> ToolResult:
    import uiautomation as auto

    from jarvis.tools.desktop import _clipboard_get, _clipboard_set, focus_hwnd

    webbrowser.open("https://www.instagram.com/direct/inbox/")
    win = _browser_window("instagram", 25, ctx)
    if not win:
        raise ToolError("Instagram didn't open in the browser (is a browser installed and are you online?)")
    if "log in" in win["title"].lower() or "login" in win["title"].lower():
        raise ToolError("Instagram is asking you to log in - log in once in the browser, then ask me again")
    focus_hwnd(win["hwnd"])
    ctx.progress("Instagram inbox open - finding the conversation")
    target = recipient.lower().strip()
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(win["hwnd"])
        convo = None
        for _ in range(12):  # the inbox list loads asynchronously
            ctx.check()
            convo = _uia_find(root, lambda c: target in (c.Name or "").lower()
                              and c.ControlTypeName in ("ButtonControl", "HyperlinkControl", "ListItemControl",
                                                        "TextControl", "GroupControl")
                              and c.BoundingRectangle.width() > 0)
            if convo:
                break
            time.sleep(1.0)
        if convo is None:
            # start a new conversation through the search dialog
            new_btn = _uia_find(root, lambda c: (c.Name or "").lower() in ("new message", "send message", "message")
                                and c.ControlTypeName in ("ButtonControl", "HyperlinkControl"))
            if new_btn is None:
                raise ToolError(f"couldn't find '{recipient}' in your Instagram inbox")
            _click(new_btn)
            time.sleep(1.5)
            search = _uia_find(root, lambda c: c.ControlTypeName == "EditControl"
                               and "search" in (c.Name or "").lower())
            if search is None:
                raise ToolError("couldn't find the Instagram search box")
            _click(search)
            search.SendKeys(recipient, interval=0.03)
            time.sleep(2.5)
            convo = _uia_find(root, lambda c: target in (c.Name or "").lower()
                              and c.ControlTypeName in ("CheckBoxControl", "ButtonControl", "ListItemControl",
                                                        "TextControl") and c.BoundingRectangle.width() > 0
                              and c.BoundingRectangle.top > search.BoundingRectangle.bottom)
            if convo is None:
                raise ToolError(f"no Instagram account matching '{recipient}' appeared in search")
            _click(convo)
            time.sleep(1.0)
            chat_btn = _uia_find(root, lambda c: (c.Name or "").lower() in ("chat", "next")
                                 and c.ControlTypeName == "ButtonControl")
            if chat_btn:
                _click(chat_btn)
        else:
            _click(convo)
        ctx.progress(f"opened the chat with {recipient} - typing the message")
        box = None
        for _ in range(10):
            box = _uia_find(root, lambda c: c.ControlTypeName in ("EditControl", "DocumentControl", "GroupControl")
                            and (c.Name or "").lower().startswith("message")
                            and c.BoundingRectangle.width() > 0)
            if box:
                break
            time.sleep(0.8)
        if box is None:
            raise ToolError("couldn't find the message box in the Instagram chat")
        header_ok = _uia_find(root, lambda c: target in (c.Name or "").lower() and c.BoundingRectangle.width() > 0)
        if header_ok is None:
            raise ToolError(f"the open chat doesn't look like {recipient}'s - not sending")
        _click(box)
        import pyautogui

        saved = _clipboard_get()
        _clipboard_set(message)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        _clipboard_set(saved)
        ctx.check()
        pyautogui.press("enter")
        # verify: the message text now appears in the conversation and the box is empty again
        snippet = message.strip()[:30].lower()
        for _ in range(10):
            time.sleep(0.8)
            seen = _uia_find(root, lambda c: snippet in (c.Name or "").lower() and c.ControlTypeName != "EditControl")
            if seen is not None:
                return ToolResult(True, output=f"Message sent to {recipient} on Instagram — I can see it in the chat.",
                                  data={"verified": True})
    from jarvis.services.screen import ScreenService

    if ScreenService.find_text(" ".join(message.split()[:3])):
        return ToolResult(True, output=f"Message sent to {recipient} on Instagram — it's visible on screen.",
                          data={"verified": True})
    return ToolResult(False, error="I pressed send but couldn't confirm the message appeared - please check the chat")
