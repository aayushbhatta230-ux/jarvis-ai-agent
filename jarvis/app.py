"""JARVIS application core: wires services together and runs the conversation.

``Jarvis`` owns every subsystem. The UI and voice pipeline call
:meth:`Jarvis.handle_input`; all heavy work runs on worker threads and
reports back through the event bus.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
import uuid
from typing import Any

from jarvis.agent import prompts
from jarvis.agent.engine import AgentEngine, spoken_summary
from jarvis.core import paths
from jarvis.core.cancel import CancelToken
from jarvis.core.config import Config
from jarvis.core.db import Database
from jarvis.core.events import EventBus, State
from jarvis.core.permissions import PermissionManager
from jarvis.memory.store import MemoryStore
from jarvis.providers.router import ModelRouter, NoModelAvailable
from jarvis.services.diagnostics import Diagnostics
from jarvis.services.scheduler import Scheduler
from jarvis.services.screen import ScreenService
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

_ACTION = re.compile(
    r"(?ix)^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|i\s+need\s+you\s+to\s+|i\s+want\s+you\s+to\s+"
    r"|go\s+ahead\s+and\s+|jarvis[, ]+)*"
    r"(open|launch|start|close|quit|exit|play|pause|resume|skip|mute|unmute|turn|set|increase|decrease|raise|lower"
    r"|create|make|write|save|delete|remove|move|rename|copy|find|search|look\s+up|google|research|download|install"
    r"|uninstall|run|execute|fix|debug|build|test|commit|push|deploy|remind|schedule|add|list|show|take|read"
    r"|summari[sz]e|organi[sz]e|check|switch|go\s+to|type|click|press|scroll|minimi[sz]e|maximi[sz]e|lock|remember"
    r"|forget|note|draft|analy[sz]e|explain\s+(this|that|the\s+error|what)|index|clone|refactor|implement|update"
    r"|upgrade|kill|stop\s+the|restart|compare|summary|focus|start\s+a)\b")
_SCREENISH = re.compile(r"(?i)\b(on|at)\s+(my|the)\s+screen\b|what('?s| is) (wrong|happening|this)|this (error|window|code)"
                        r"|what am i (doing|working on)|my (open )?windows")
# An action verb anywhere in a short request ("I said, can you close VS Code?") means the agent handles it.
_ACTION_ANYWHERE = re.compile(
    r"(?i)\b(open|close|launch|start|quit|exit|kill|play|pause|resume|skip|mute|unmute|turn (on|off|up|down)"
    r"|volume|create|make|write|save|delete|remove|move|rename|copy|find|search|download|install|run|fix|build"
    r"|commit|push|remind|schedule|type|click|press|scroll|minimi[sz]e|maximi[sz]e|lock|shut ?down|restart"
    r"|screenshot|my screen|on screen|switch to|go to|send|email|e-mail|mail|inbox|text|message|dm|instagram"
    r"|whatsapp|reply|organi[sz]e|clean up|check)\b")
# A chat reply must never claim to have done something: only the agent's verified tools can do that.
_CLAIM = re.compile(
    r"(?i)\[\[|\b(i('ve| have)? (just )?(opened|closed|launched|started|created|deleted|removed|moved|saved|sent"
    r"|played|paused|muted|set|turned|installed|ran|typed|clicked)|(is|are) now (open|closed|running|playing|muted"
    r"|paused|off|on)|i('ll| will) (now |go ahead and )?(open|close|launch|start|create|delete|play|pause|turn|set"
    r"|install|run|type|click))\b")
_SHARE_ON = re.compile(r"(?i)\b((start|begin|turn on|enable|start the) (screen ?shar\w*|sharing( my)? screen)"
                       r"|share (my|the) screen|watch my screen|look at my screen live|go live on my screen)\b")
_SHARE_OFF = re.compile(r"(?i)\b((stop|end|turn off|disable|quit) (the )?(screen ?shar\w*|sharing|watching( my)? "
                        r"screen)|stop looking at my screen)\b")
_DIRECT_ACK = {"play_pause": "Done.", "mute": "Toggled mute.", "fullscreen": "Toggled full screen.",
               "captions": "Toggled captions.", "forward": "Skipped ahead.", "back": "Went back.",
               "next": "Next one.", "previous": "Previous one.", "faster": "Speeding it up.",
               "slower": "Slowing it down.", "theater": "Toggled theater mode."}
_DIRECT_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"(auto(matically)?|always|keep) skip(ping)? (the )?ads?( for me)?( on)?|turn on (auto ?)?ad ?skip"),
     lambda m: ("set_auto_skip_ads", {"on": True})),
    (re.compile(r"(stop|don'?t|turn off) (auto(matically)? )?skip(ping)? (the )?ads?|turn off (auto ?)?ad ?skip"),
     lambda m: ("set_auto_skip_ads", {"on": False})),
    (re.compile(r"(skip|close|remove|get rid of) (this |the |that )?(ad|ads|advert|advertisement)s?"),
     lambda m: ("skip_ad", {})),
    (re.compile(r"(pause|resume|play|unpause|stop)( the| this)?( video| song| music| it| track)?"),
     lambda m: ("video_control", {"action": "play_pause"})),
    (re.compile(r"(un)?mute( the| this)?( video| tab| it)?"), lambda m: ("video_control", {"action": "mute"})),
    (re.compile(r"(go |make it |exit |leave )?full ?screen"), lambda m: ("video_control", {"action": "fullscreen"})),
    (re.compile(r"(turn (on|off) |show |hide )?(captions|subtitles)( on| off)?"),
     lambda m: ("video_control", {"action": "captions"})),
    (re.compile(r"(next|skip this) (video|song|track|one)|play (the )?next( one)?"),
     lambda m: ("video_control", {"action": "next"})),
    (re.compile(r"(previous|last) (video|song|track|one)"), lambda m: ("video_control", {"action": "previous"})),
    (re.compile(r"(skip|go|jump|fast) (ahead|forward)( (\d+) seconds)?"),
     lambda m: ("video_control", {"action": "forward", "times": max(1, int(m.group(4) or 10) // 10)})),
    (re.compile(r"(rewind|go back|jump back)( (\d+) seconds)?"),
     lambda m: ("video_control", {"action": "back", "times": max(1, int(m.group(3) or 10) // 10)})),
    (re.compile(r"(speed (it )?up|play faster|faster)"), lambda m: ("video_control", {"action": "faster"})),
    (re.compile(r"(slow (it )?down|play slower|slower)"), lambda m: ("video_control", {"action": "slower"})),
    (re.compile(r"(turn |the )?volume (up|down)|(louder|quieter)"),
     lambda m: ("media_control", {"action": "volume_up" if (m.group(2) == "up" or m.group(3) == "louder")
                                  else "volume_down"})),
    (re.compile(r"set (the )?volume to (\d{1,3})( percent| %)?"), lambda m: ("set_volume", {"level": int(m.group(2))})),
    (re.compile(r"scroll (down|up)( a bit| more)?"),
     lambda m: ("scroll", {"amount": -5 if m.group(1) == "down" else 5})),
    (re.compile(r"(click|press|tap|hit)( on)?( the)? (.+?)( button| link| tab)?"),
     lambda m: ("click_on_screen", {"label": m.group(4)})),
]


def quick_action(text: str) -> tuple[str, dict[str, Any]] | None:
    """Map a short real-time command to a tool call, or None if it needs the full agent."""
    t = re.sub(r"(?i)\b(hey|hi|ok|okay|jarvis|please|can you|could you|would you|just|now|for me|quickly)\b[,]?",
               " ", text)
    t = re.sub(r"\s+", " ", t).strip(" ?.!,").lower()
    if not t or len(t.split()) > 7:
        return None
    for pattern, build in _DIRECT_PATTERNS:
        m = pattern.fullmatch(t)
        if m:
            return build(m)
    return None


_STOP = re.compile(r"(?i)^\s*(stop|cancel|abort|never ?mind|shut up|be quiet|quiet|that'?s enough|enough)\W*$")
_YES = re.compile(r"(?i)^\s*(yes|yeah|yep|sure|go ahead|do it|approve|approved|confirm|ok(ay)?|affirmative)\b")
_NO = re.compile(r"(?i)^\s*(no|nope|don'?t|do not|deny|denied|cancel|stop|negative)\b")
_RESUME = re.compile(r"(?i)^\s*(resume|continue)( (the|my|that))?( last)?( task)?\W*$")


class Jarvis:
    def __init__(self, config: Config | None = None, db: Database | None = None) -> None:
        self.config = config or Config()
        self.events = EventBus()
        self.db = db or Database()
        try:
            from jarvis.core.hardware import adapt

            adapt(self.config, self.db)
        except Exception:  # noqa: BLE001 - tuning is best effort
            log.exception("hardware adaptation failed")
        self.permissions = PermissionManager(self.config, self.events, self.db)
        self.router = ModelRouter(self.config, self.events)
        self.memory = MemoryStore(self)
        self.tools = ToolRegistry(self)
        self.tools.load_builtin()
        self.screen = ScreenService(self)
        self.agent = AgentEngine(self)
        self.scheduler = Scheduler(self)
        self.diagnostics = Diagnostics(self)
        self.voice: Any = None
        self.voice_error = ""
        self.session = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.history: list[dict[str, str]] = []
        self._inputs: queue.Queue[tuple[str, str]] = queue.Queue()
        self._chat_cancel = CancelToken()
        self._processing = threading.Event()
        self._stopping = threading.Event()
        self.events.subscribe("agent.done", self._on_agent_done)
        self.events.subscribe("tts.idle", self._on_tts_idle)
        self.events.subscribe("permission.request", self._on_permission_request)
        self.events.subscribe("screen.alert", self._on_screen_alert)

    # ------------------------------------------------------------- lifecycle
    def start(self, voice: bool = True) -> None:
        try:
            imported = self.memory.import_legacy(paths.PROJECT_ROOT)
            if imported:
                log.info("imported %d items from the previous JARVIS memory files", imported)
        except Exception:  # noqa: BLE001
            log.exception("legacy import failed")
        self.scheduler.start()
        self.screen.start_observer()
        threading.Thread(target=self._input_loop, name="jarvis-conversation", daemon=True).start()
        threading.Thread(target=self._warm_up, name="jarvis-warmup", daemon=True).start()
        threading.Thread(target=self._daily_maintenance, name="jarvis-maintenance", daemon=True).start()
        threading.Thread(target=self._keep_warm, name="jarvis-keep-warm", daemon=True).start()
        if self.config.get("live.auto_skip_ads", False):
            from jarvis.tools.live import AdSkipWatcher

            AdSkipWatcher.ensure(self)
        self._ensure_briefing_job()
        if voice and self.config.get("voice.enabled", True):
            threading.Thread(target=self._start_voice, name="jarvis-voice-init", daemon=True).start()
        self.events.state(State.IDLE, "online")

    def _start_voice(self) -> None:
        try:
            from jarvis.voice.pipeline import VoiceManager

            self.events.publish("notice", text="Loading voice models...")
            vm = VoiceManager(self, self._on_voice_utterance)
            vm.start()
            self.voice = vm
            self.events.publish("voice.ready", **vm.status())
        except Exception as exc:  # noqa: BLE001 - voice is optional; text still works
            log.exception("voice pipeline failed to start")
            self.voice_error = f"{exc.__class__.__name__}: {exc}"
            self.events.publish("voice.error", where="startup", error=self.voice_error)
            self.events.publish("notice", text=f"Voice unavailable ({exc}). Typing still works.")

    def _warm_up(self) -> None:
        try:
            cand = self.router.resolve("chat")
            self.router.chat("chat", [{"role": "user", "content": "hi"}], max_tokens=1)
            log.info("warmed up %s", cand.key)
        except Exception as exc:  # noqa: BLE001
            log.warning("model warm-up failed: %s", exc)
            self.events.publish("notice", text=f"No language model ready yet: {str(exc)[:160]}")
        try:
            self.memory.retry_embeddings()
        except Exception:  # noqa: BLE001
            pass

    def _daily_maintenance(self) -> None:
        while not self._stopping.wait(5):
            last = self.db.kv_get("last_backup", 0)
            if time.time() - last > 86400:
                try:
                    self.db.backup()
                    self.db.kv_set("last_backup", time.time())
                    log.info("daily database backup written")
                except Exception:  # noqa: BLE001
                    log.exception("backup failed")
            self._stopping.wait(3600)

    def _ensure_briefing_job(self) -> None:
        cfg = self.config.get("productivity.daily_briefing", {})
        existing = [j for j in self.scheduler.list() if j["kind"] == "briefing"]
        if cfg.get("enabled") and not existing:
            self.scheduler.add("Daily briefing", "briefing", {}, {"type": "daily", "time": cfg.get("time", "08:30")})

    def shutdown(self) -> None:
        self._stopping.set()
        self.events.publish("app.shutdown")
        self.agent.shutdown()
        self.scheduler.stop()
        self.screen.stop_observer()
        if self.voice is not None:
            self.voice.stop()
        from jarvis.tools.shell import PROCESSES

        PROCESSES.stop_all()
        self.tools.shutdown()
        self.db.close()

    # ---------------------------------------------------------- input paths
    def _on_voice_utterance(self, text: str, verification: Any) -> None:
        self.submit_input(text, "voice")

    def submit_input(self, text: str, source: str = "text") -> None:
        text = text.strip()
        if not text:
            return
        # Interrupt words act immediately, even while something else runs.
        if _STOP.match(text):
            self.stop_everything()
            self.events.publish("user.message", text=text, source=source)
            return
        self._inputs.put((text, source))

    def stop_everything(self) -> None:
        self._chat_cancel.cancel("stopped by user")
        if self.voice is not None:
            self.voice.interrupt()
        n = self.agent.cancel()
        self.events.state(State.IDLE, "stopped")
        if n:
            self.events.publish("assistant.message", text=f"Stopped {n} running task{'s' if n > 1 else ''}.",
                                final=True)

    def busy(self) -> bool:
        return self._processing.is_set() or bool(self.agent.active())

    def _input_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                text, source = self._inputs.get(timeout=0.5)
            except queue.Empty:
                continue
            self._processing.set()
            try:
                self.handle_input(text, source)
            except Exception as exc:  # noqa: BLE001
                log.exception("handling input failed")
                self.events.state(State.RECOVERING, "error")
                self.say(f"Something went wrong handling that: {exc}")
            finally:
                self._processing.clear()

    def handle_input(self, text: str, source: str = "text") -> None:
        self.events.publish("user.message", text=text, source=source)
        self.memory.add_message(self.session, "user", text, {"source": source})
        self.history.append({"role": "user", "content": text})

        # Pending permission request answered by voice/text
        if self.permissions.pending():
            if _YES.match(text):
                self.permissions.respond_latest(True)
                self.say("Approved.")
                return
            if _NO.match(text):
                self.permissions.respond_latest(False)
                self.say("Understood, I won't do that.")
                return
        # A running task asked the user something
        waiting = self.agent.waiting_question()
        if waiting is not None:
            self.agent.answer(text, waiting.id)
            self.events.state(State.EXECUTING, "continuing")
            return
        if _RESUME.match(text):
            row = self.db.one("SELECT id, goal FROM tasks WHERE status IN ('interrupted','failed','paused') "
                              "ORDER BY updated DESC LIMIT 1")
            if row:
                self.agent.resume(row["id"])
                self.say(f"Resuming: {row['goal'][:80]}")
                return
        quick = self._quick_answer(text)
        if quick:
            self.history.append({"role": "assistant", "content": quick})
            self.say(quick, speak=source == "voice")
            return
        share = _SHARE_ON.search(text), _SHARE_OFF.search(text)
        if share[1] or share[0]:
            on = not share[1]
            self.screen.set_sharing(on)
            reply = ("I can see your screen now. Ask me anything about it." if on
                     else "Okay, I've stopped watching your screen.")
            self.history.append({"role": "assistant", "content": reply})
            self.say(reply, speak=source == "voice")
            return
        if self._is_screen_question(text):
            self._screen_chat(text, source)
            return
        direct = quick_action(text)
        if direct is not None:
            self._run_direct(direct, text, source)
            return
        if _ACTION.match(text) or _SCREENISH.search(text) or (len(text.split()) <= 16 and
                                                               _ACTION_ANYWHERE.search(text)):
            self._start_task(text, source)
            return
        self._chat(text, source)

    def _run_direct(self, action: tuple[str, dict[str, Any]], text: str, source: str) -> None:
        """Execute a recognised real-time command immediately (no planning round-trips) and report the result."""
        tool, args = action
        self.events.state(State.EXECUTING, tool)
        t0 = time.time()
        res = self.tools.execute(tool, args)
        log.info("direct action %s%s -> %s in %.1fs", tool, args, "ok" if res.ok else res.error[:80], time.time() - t0)
        if res.ok:
            reply = res.output.split("\n")[0] if tool not in ("video_control",) else _DIRECT_ACK.get(
                args.get("action", ""), "Done.")
            self.events.state(State.COMPLETED, tool)
        else:
            reply = f"Couldn't do that: {res.error}"
            self.events.state(State.RECOVERING, tool)
        self.history.append({"role": "assistant", "content": reply})
        self.memory.add_message(self.session, "assistant", reply, {"direct": tool, "ok": res.ok})
        self.say(reply, speak=source == "voice")

    @staticmethod
    def _quick_answer(text: str) -> str:
        """Instant replies that need no model (time / date)."""
        t = re.sub(r"\b(hey|hi|ok|jarvis|please|can you tell me|tell me)\b[,]?", " ", text.lower())
        t = re.sub(r"\s+", " ", t).strip(" ?.!,")
        if re.fullmatch(r"(what('?s| is) the )?time( is it)?( now| right now)?|what time is it( now)?|time", t):
            return time.strftime("It's %I:%M %p.").replace(" 0", " ")
        if re.fullmatch(r"(what('?s| is) )?(today'?s |the )?(date|day)( today)?|what day is (it|today)", t):
            return time.strftime("It's %A, %d %B %Y.")
        return ""

    def _keep_warm(self) -> None:
        """Low RAM makes Windows page out an idle model; a tiny request every few minutes keeps replies fast."""
        while not self._stopping.wait(240):
            if self.busy() or (self.voice is not None and self.voice.speaker.speaking):
                continue
            try:
                self.router.chat("chat", [{"role": "user", "content": "ok"}], max_tokens=1)
            except Exception:  # noqa: BLE001
                pass

    def _start_task(self, goal: str, source: str) -> None:
        if source == "voice":
            self.say(self._ack())
        run = self.agent.submit(goal, source=f"conversation:{source}", history=self.history[:-1])
        self.events.publish("assistant.task_started", task_id=run.id, goal=goal)

    @staticmethod
    def _ack() -> str:
        import random

        return random.choice(["On it.", "Right away.", "Working on it.", "Give me a moment.", "Certainly."])

    def _is_screen_question(self, text: str) -> bool:
        """'What's on my screen?', 'explain this error' - questions answered by looking, not by doing."""
        t = text.lower()
        if not (_SCREENISH.search(t) or re.search(r"\b(screen|monitor)\b|\bthis (error|code|window|page|message)\b",
                                                  t)):
            return False
        rest = re.sub(r"\b(my screen|on screen|the screen|screen|monitor|check|read|look|see|explain|tell me|show)\b",
                      " ", t)
        return not _ACTION_ANYWHERE.search(rest)

    def _screen_chat(self, text: str, source: str) -> None:
        """Answer about the screen in ONE model call: a fresh frame + OCR (vision model if a fast one exists)."""
        self.events.state(State.SCREEN, "looking at your screen")
        img, ocr = self.screen.latest_frame(max_age=2.0 if self.screen.sharing else 0.0)
        images: list[str] = []
        role = "chat"
        try:
            cand = self.router.resolve("vision")
            local_vision = self.router.providers[cand.provider].type == "ollama"
            # A local vision model must swap in (10-60 s on this laptop); with OCR the chat model answers in ~2 s.
            if not local_vision or re.search(r"(?i)\b(look like|colou?r|picture|image|photo|video|design|layout)\b",
                                             text):
                from jarvis.services.screen import to_b64_png

                images, role = [to_b64_png(img, 1280)], "vision"
        except NoModelAvailable:
            pass
        context = ("[What's on the user's screen right now - OCR text, may contain recognition errors]\n"
                   + (re.sub(r"\n\s*\n+", "\n", ocr).strip()[:2500] or "(no readable text)"))
        self._chat(text, source, role=role, images=images, extra=prompts.SCREEN_RULES + "\n\n" + context)

    def _chat(self, text: str, source: str, role: str = "chat", images: list[str] | None = None,
              extra: str = "") -> None:
        self._chat_cancel = CancelToken()
        self.events.state(State.PROCESSING, "thinking")
        cfg = self.config.snapshot()
        memory = self.memory.context_block(text, fast=True)  # keyword-only: no embedding round-trip
        screen = ""
        if not extra and self.screen.sharing:
            _img, ocr = self.screen.latest_frame(max_age=5.0)
            screen = f"Active window: {self.screen.context.window_title}\n{ocr.strip()[:2000]}"
        elif self.config.get("screen.observe") == "continuous" and self.screen.context.ts:
            screen = self.screen.context.summary(800)
        system = "\n\n".join(filter(None, [
            prompts.identity(cfg), prompts.VOICE_INTERPRETATION, prompts.CHAT_RULES, prompts.SAFETY,  # static first
            prompts.environment_block(self.db.kv_get("last_project")),
            ("What you remember:\n" + memory) if memory else "",
            ("The user is sharing their screen with you. Right now it shows:\n" + screen) if screen else "",
            extra]))
        messages = [{"role": "system", "content": system}] + self.history[-12:]
        if images:
            messages[-1] = {**messages[-1], "images": images}
        buffer: list[str] = []
        decided = {"action": None}
        speak = source == "voice" and self.voice is not None

        def on_text(piece: str) -> None:
            buffer.append(piece)
            if decided["action"] is None:
                head = "".join(buffer).lstrip()
                first_sentence_done = bool(re.search(r"[.!?\n]", head[8:])) or len(head) > 160
                if not head or (len(head) < 10 and "[[ACTION]]".startswith(head)):
                    return  # could still be the hand-off marker: hold back
                if head.startswith("[[") or _CLAIM.search(head):
                    decided["action"] = True
                    return
                if not first_sentence_done:
                    return  # hold until the first sentence can be checked for false claims
                decided["action"] = False
                piece = "".join(buffer)  # release what was held back
                self.events.state(State.SPEAKING if speak else State.PROCESSING)
            if decided["action"]:
                return
            self.events.publish("assistant.delta", text=piece)
            if speak:
                self.voice.speaker.feed(piece)

        t0 = time.time()
        try:
            result = self.router.chat(role, messages, on_text=on_text, cancel=self._chat_cancel, max_tokens=700,
                                      temperature=0.6)
            log.info("chat reply in %.1fs (%s)", time.time() - t0, source)
        except NoModelAvailable as exc:
            self.say("I can't reach a language model right now. " + ("Ollama doesn't seem to be running."
                                                                      if "ollama" in str(exc).lower() else ""))
            self.events.publish("notice", text=str(exc)[:300])
            return
        reply = result.text.strip()
        if decided["action"] or reply.startswith("[[") or _CLAIM.search(reply):
            # The chat model tried to answer an action itself (it has no tools) - hand it to the agent.
            if speak:
                self.voice.speaker.stop()
            log.info("chat reply looked like an action claim; routing to the agent: %r", reply[:120])
            self.history.pop()
            self._start_task(text, source)
            return
        if decided["action"] is None and reply:  # short reply that never reached the release point
            self.events.publish("assistant.delta", text=reply)
            if speak:
                self.voice.speaker.feed(reply)
        if speak:
            self.voice.speaker.flush()
        self.history.append({"role": "assistant", "content": reply})
        self.memory.add_message(self.session, "assistant", reply, {"model": f"{result.provider}:{result.model}"})
        self.events.publish("assistant.message", text=reply, final=True, model=f"{result.provider}:{result.model}")
        if not speak:
            self.events.state(State.IDLE)

    # --------------------------------------------------------------- output
    def say(self, text: str, speak: bool = True) -> None:
        self.events.publish("assistant.message", text=text, final=True)
        if speak and self.voice is not None:
            self.voice.say(text)
        elif not self.busy():
            self.events.state(State.IDLE)

    def notify(self, title: str, message: str, speak: bool = False) -> None:
        self.events.publish("notify", title=title, message=message)
        if speak and self.voice is not None:
            self.voice.say(f"{title}. {message}" if title not in ("JARVIS", "Reminder") else message)

    def _on_agent_done(self, ev: Any) -> None:
        d = ev.data
        source = d.get("source", "")
        result = d.get("result", "")
        status = d.get("status")
        prefix = {"completed_unverified": "⚠ Not verified: ", "failed": "I couldn't finish that. ",
                  "cancelled": ""}.get(status, "")
        if source.startswith("conversation"):
            self.history.append({"role": "assistant", "content": result})
            self.memory.add_message(self.session, "assistant", result, {"task": d.get("task_id"), "status": status})
            self.events.publish("assistant.message", text=prefix + result, final=True, task_id=d.get("task_id"))
            if source.endswith("voice") and self.voice is not None and status != "cancelled":
                lead = {"failed": "I couldn't finish that. ",
                        "completed_unverified": "I tried, but I couldn't confirm it worked. "}.get(status, "")
                self.voice.say(lead + spoken_summary(result))
        elif source.startswith("schedule") and status == "failed":
            self.notify("Scheduled task failed", f"{d.get('goal', '')[:80]}: {result[:200]}")

    def _on_tts_idle(self, ev: Any) -> None:
        if self.voice is not None and not self.busy():
            self.voice.follow_up()

    def _on_permission_request(self, ev: Any) -> None:
        d = ev.data
        if self.voice is not None:
            self.voice.say(f"I need your approval to {d['summary'][:140]}. Say yes or no.")
        self.events.publish("notify", title="Approval needed", message=d["summary"][:200])

    def _on_screen_alert(self, ev: Any) -> None:
        lines = ev.data.get("lines") or []
        if lines:
            self.events.publish("notify", title=f"Possible error in {ev.data.get('window', '')[:40]}",
                                message=lines[0][:200])

    # -------------------------------------------------------------- briefing
    def briefing(self) -> str:
        now = time.time()
        user = self.config.get("user.name", "")
        parts = [f"Good {'morning' if time.localtime().tm_hour < 12 else 'afternoon' if time.localtime().tm_hour < 18 else 'evening'}"
                 f"{', ' + user if user else ''}. It's {time.strftime('%A %d %B, %H:%M')}."]
        todos = self.db.query("SELECT title, due FROM todos WHERE status='open' ORDER BY (due IS NULL), due, priority "
                              "LIMIT 8")
        overdue = [t for t in todos if t["due"] and t["due"] < now]
        if todos:
            parts.append(f"You have {len(todos)} open to-do{'s' if len(todos) != 1 else ''}"
                         + (f", {len(overdue)} overdue" if overdue else "") + ": "
                         + "; ".join(t["title"] for t in todos[:5]) + ".")
        else:
            parts.append("Your to-do list is clear.")
        jobs = [j for j in self.scheduler.list(False) if j["next_run"] and j["next_run"] < now + 86400]
        if jobs:
            parts.append("Coming up: " + "; ".join(f"{j['name']} at {j['next_run_text']}" for j in jobs[:5]) + ".")
        unfinished = self.db.query("SELECT goal FROM tasks WHERE status IN ('interrupted','failed','paused') "
                                   "ORDER BY updated DESC LIMIT 3")
        if unfinished:
            parts.append("Unfinished tasks: " + "; ".join(u["goal"][:60] for u in unfinished) + ".")
        from jarvis.tools.system import system_snapshot

        snap = system_snapshot()
        parts.append(f"System: {snap['ram_used_pct']:.0f}% RAM used, {snap['disk_c_free_gb']} GB free on C:."
                     + (f" Battery {snap['battery']['percent']:.0f}%." if snap.get("battery") else ""))
        return " ".join(parts)
