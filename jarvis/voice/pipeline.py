"""Voice conversation pipeline.

    mic -> 16 kHz frames -> [wake word | VAD segmentation] -> speaker check -> STT -> assistant
    assistant text -> sentence streaming TTS -> headset output (barge-in stops it)

Runs on its own threads so capture, recognition, synthesis, the GUI and agent
tasks never block one another. Device changes (headset connected or removed)
are picked up automatically.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from jarvis.core import paths
from jarvis.core.events import State
from jarvis.voice import devices
from jarvis.voice.capture import MicCapture
from jarvis.voice.speaker_id import SpeakerRecognizer, Verification, VoiceprintStore
from jarvis.voice.stt import STT, has_wake_word, strip_wake_word
from jarvis.voice.tts import Speaker
from jarvis.voice.vad import load_vad
from jarvis.voice.wakeword import load_wakeword

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
FRAME_S = 512 / 16000

ENROLL_PHRASES = [
    "Hey JARVIS, this is my voice. Please learn to recognise it.",
    "Open my project folder and check whether the tests are passing.",
    "Remind me to call the bank tomorrow at ten in the morning.",
    "What's on my screen right now, and is anything wrong with it?",
    "Play some music and turn the volume down a little.",
]


_BACKCHANNEL_WORD = (
    r"(ok(ay)?|yeah|yes|yep|ya|haan|ha|hmm+|mm+|uh+( huh)?|um+|ah+|oh|right|sure|nice|cool|wow|sorry|"
    r"go on|go ahead|continue|carry on|keep going|why (did )?you stop|i'?m listening|nothing|never ?mind)")
_BACKCHANNEL = re.compile(r"(?i)^\s*" + _BACKCHANNEL_WORD + r"([\s.!?,]+" + _BACKCHANNEL_WORD + r")*[\s.!?,]*$")


_ADDRESSED = re.compile(
    r"(?i)(\?\s*$|^\s*(what|why|how|when|where|who|which|whose|can|could|would|will|should|do|does|did|is|are|am|"
    r"tell|show|open|close|play|pause|stop|skip|search|find|send|text|email|call|remind|set|turn|make|write|read|"
    r"check|explain|help|give|take|go|start|create|delete|move|copy|thanks|thank|yes|no|okay|ok|sure)\b|"
    r"\b(you|your|jarvis|please|my screen|this|that)\b)")
_HINGLISH = re.compile(r"(?i)\b(hai|nahi|nahin|kya|mein|ko|ka|ki|ke|yaar|bhai|kasam|haan|accha|achha|theek|kar|"
                       r"raha|rahi|tha|thi|bol|bolo|dena|chal|abhi|mujhe|tum|tu|hum|aur|bhi|samajh\w*)\b")


def looks_addressed(text: str) -> bool:
    """In the follow-up window, only react to speech that sounds like it's meant for JARVIS."""
    words = text.split()
    if len(_HINGLISH.findall(text)) >= 2 and not re.search(r"(?i)\bjarvis\b", text):
        return False  # a side conversation in Hindi/Nepali
    if len(words) <= 2:
        return bool(_ADDRESSED.search(text)) and not re.fullmatch(r"(?i)\s*(and|so|um+|uh+|the)\W*(and)?\W*", text)
    return bool(_ADDRESSED.search(text)) or len(words) >= 6


def is_backchannel(text: str) -> bool:
    """Short reactions that shouldn't cancel what JARVIS is saying."""
    t = (text or "").strip()
    return not t or bool(_BACKCHANNEL.match(t))


class VoiceManager:
    PASSIVE, ACTIVE = "passive", "active"

    def __init__(self, app: Jarvis, on_utterance: Callable[[str, Verification], None]) -> None:
        self.app = app
        self.cfg = app.config
        self.on_utterance = on_utterance
        models = paths.models_dir()
        self._last_level_pub = 0.0
        self.mic = MicCapture(on_level=lambda lv: self._level("mic", lv))
        self.speaker = Speaker(
            backend=self.cfg.get("voice.tts_backend", "edge"), voice=self.cfg.get("voice.tts_voice"),
            rate_pct=int(self.cfg.get("voice.tts_rate", 0)), volume_pct=int(self.cfg.get("voice.tts_volume", 100)),
            on_level=lambda lv: self._level("tts", lv), on_state=self._tts_state)
        self.vad = load_vad(models)
        self.wake = load_wakeword(models)  # loaded even when off, so the setting can change live
        self.stt = STT(models, self.cfg.get("voice.stt_backend", "google"), self.cfg.get("voice.stt_model", "base.en"),
                       self.cfg.get("voice.stt_language", "en-IN"))
        self.wake_aliases: list[str] = list(app.db.kv_get("wake_aliases", []) or [])
        self._tap: list[np.ndarray] | None = None
        self._speech_end = 0.0
        try:
            self.recognizer: SpeakerRecognizer | None = SpeakerRecognizer(models)
        except Exception as exc:  # noqa: BLE001
            log.warning("speaker recognition unavailable: %s", exc)
            self.recognizer = None
        self.voiceprints = VoiceprintStore(app.db)
        self._load_voiceprint()

        self.mode = self.PASSIVE if self.wake else self.ACTIVE
        self._active_deadline = 0.0
        self._in_speech = False
        self._speech: list[np.ndarray] = []
        self._silence = 0
        self._voiced = 0
        self._preroll: deque[np.ndarray] = deque(maxlen=10)
        self._recent: deque[np.ndarray] = deque(maxlen=60)  # ~2 s for wake-word speaker check
        self._wake_refractory = 0.0
        self._barge_frames = 0
        self._interrupted = False  # a reply was paused by barge-in and may be resumed
        self._passive_speech = 0  # frames of speech heard while waiting for the wake word
        self._passive_quiet = 0
        self._passive_best = 0.0
        self._passive_audio: list[np.ndarray] = []
        self._jobs: queue.Queue[tuple[np.ndarray, str]] = queue.Queue()
        self._enroll_sink: queue.Queue[np.ndarray] | None = None
        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._last_frame = time.time()
        self.muted = False
        self.input_device: devices.Device | None = None
        self.output_device: devices.Device | None = None
        self.hotplug = devices.HotplugWatcher(self._on_hotplug)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._open_devices()
        threading.Thread(target=self._loop, name="jarvis-voice", daemon=True).start()
        threading.Thread(target=self._process_loop, name="jarvis-stt", daemon=True).start()
        self.hotplug.start()
        self._publish_status()

    def stop(self) -> None:
        self._stop.set()
        self.hotplug.stop()
        self.speaker.close()
        self.mic.close()

    def _open_devices(self) -> None:
        prefer = bool(self.cfg.get("voice.prefer_headset", True))
        self.input_device = devices.choose_input(self.cfg.get("voice.input_device", ""), prefer,
                                                 bool(self.cfg.get("voice.avoid_builtin_mic", True)))
        self.output_device = devices.choose_output(self.cfg.get("voice.output_device", ""), prefer)
        try:
            self.speaker.open(self.output_device)
        except Exception as exc:  # noqa: BLE001
            log.error("speaker output failed: %s", exc)
            self.app.events.publish("voice.error", where="output", error=str(exc))
        try:
            self.mic.open(self.input_device)
            self._last_frame = time.time()
        except Exception as exc:  # noqa: BLE001
            log.error("microphone failed: %s", exc)
            self.app.events.publish("voice.error", where="input", error=str(exc))
        if self.input_device and not devices.is_headset(self.input_device.name):
            self.app.events.publish("notice", text=f"Headset microphone not detected - using "
                                    f"'{self.input_device.name}'. Connect your headset and I'll switch automatically.")

    def reopen(self) -> None:
        """Re-select devices (e.g. after a settings change or hot-plug)."""
        self._reopen.set()

    def _do_reopen(self) -> None:
        self.speaker.stop()
        self.speaker.player.close()
        self.mic.close()
        try:
            devices.refresh_portaudio()
        except Exception:  # noqa: BLE001
            log.exception("PortAudio refresh failed")
        self._open_devices()
        self._publish_status()

    def _on_hotplug(self, added: frozenset[str], removed: frozenset[str]) -> None:
        self.app.events.publish("voice.devices_changed", added=sorted(added), removed=sorted(removed))
        self.reopen()

    def _publish_status(self) -> None:
        self.app.events.publish("voice.status", **self.status())

    def status(self) -> dict[str, Any]:
        rec = self.recognizer
        return {
            "input": self.input_device.name if self.input_device else None,
            "input_is_headset": bool(self.input_device and devices.is_headset(self.input_device.name)),
            "output": self.output_device.name if self.output_device else None,
            "mic_open": self.mic.active,
            "mic_error": self.mic.error,
            "wake_word": self._wake_enabled(),
            "mode": self.mode,
            "stt": self.stt.primary,
            "tts": self.speaker.last_engine or ("edge" if self.speaker.primary else "sapi"),
            "speaker_id": "unavailable" if rec is None else ("enrolled" if rec.enrolled else "not enrolled"),
            "speaker_id_mode": self.cfg.get("voice.speaker_id.mode", "enforce"),
            "wake_trained": bool(self.wake_aliases),
            "muted": self.muted,
        }

    # ------------------------------------------------------------- controls
    def push_to_talk(self) -> None:
        self.interrupt()
        self._go_active(8.0)

    def interrupt(self) -> None:
        if self.speaker.speaking:
            self.speaker.stop()
            self.app.events.publish("voice.interrupted")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._reset_segment()
        self._publish_status()

    def say(self, text: str) -> None:
        self.speaker.say(text)

    def follow_up(self) -> None:
        """Keep listening for a follow-up without the wake word."""
        self._go_active(float(self.cfg.get("voice.follow_up_seconds", 10.0)), reason="followup")

    def _go_active(self, seconds: float, reason: str = "wake") -> None:
        if self.mode != self.ACTIVE:
            self.vad.reset()
        self._active_reason = reason
        self.mode = self.ACTIVE
        self._active_deadline = time.time() + seconds
        self.app.events.state(State.LISTENING, "listening")

    def _go_passive(self) -> None:
        if not self._wake_enabled():
            self.mode = self.ACTIVE  # always-listening mode: no wake word needed
            self._active_deadline = float("inf")
            return
        self.mode = self.PASSIVE
        self._reset_segment()
        if not self.speaker.speaking and not self.app.busy():
            self.app.events.state(State.IDLE)

    def _reset_segment(self) -> None:
        self._in_speech = False
        self._speech = []
        self._silence = 0
        self._voiced = 0

    # ------------------------------------------------------------ main loop
    def _level(self, source: str, level: float) -> None:
        now = time.time()
        if now - self._last_level_pub > 0.04:
            self._last_level_pub = now
            self.app.events.publish("audio.level", source=source, level=min(1.0, level * 6))

    def _tts_state(self, speaking: bool) -> None:
        if speaking:
            self.app.events.state(State.SPEAKING)
        else:
            self.app.events.publish("tts.idle")

    def _loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self._reopen.is_set():
                self._reopen.clear()
                self._do_reopen()
            try:
                frame = self.mic.frames.get(timeout=0.5)
                self._last_frame = time.time()
                backoff = 1.0
            except queue.Empty:
                if time.time() - self._last_frame > 4.0 and not self.muted:
                    log.warning("no audio frames for 4s; reopening microphone")
                    self.app.events.publish("voice.error", where="input", error="microphone stopped delivering audio")
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30)
                    self._do_reopen()
                    self._last_frame = time.time()
                continue
            if self._tap is not None:
                self._tap.append(frame)
            if self.muted:
                continue
            try:
                self._handle(frame)
            except Exception:  # noqa: BLE001
                log.exception("voice frame handling failed")

    def _handle(self, frame: np.ndarray) -> None:
        self._recent.append(frame)
        if self.mode == self.PASSIVE and not self._wake_enabled():
            self._go_passive()  # switched to always-listening at runtime
        speaking = self.speaker.speaking
        prob = self.vad(frame)

        # Wake word (also allows interrupting JARVIS by saying "Hey JARVIS")
        if self._wake_enabled() and (self.mode == self.PASSIVE or speaking):
            score = self.wake.process(frame)
            if self.mode == self.PASSIVE and not speaking:
                self._track_passive(frame, prob, score)
            if score >= float(self.cfg.get("voice.wake_threshold", 0.5)) and time.time() > self._wake_refractory:
                self._wake_refractory = time.time() + 1.5
                audio = np.concatenate(list(self._recent)[-45:])
                log.info("wake word detected (score %.2f)", score)
                self._passive_speech = self._passive_quiet = 0
                self._passive_audio = []
                self._passive_best = 0.0
                if self._gate(audio, "wake"):
                    self.app.events.publish("voice.wake", score=round(score, 2))
                    self.interrupt()
                    self._reset_segment()
                    self._go_active(8.0)
                return

        # Barge-in: user starts talking over JARVIS (headset => no speaker echo)
        # Over loudspeakers JARVIS would hear itself, so only the wake word may interrupt there.
        out_is_headset = bool(self.output_device and devices.is_headset(self.output_device.name))
        if speaking and self.cfg.get("voice.barge_in", True) and out_is_headset:
            self._barge_frames = self._barge_frames + 1 if prob > 0.6 else max(0, self._barge_frames - 2)
            need = int(float(self.cfg.get("voice.barge_in_seconds", 0.6)) / FRAME_S)
            if self._barge_frames >= need:  # sustained speech, not a cough or a click
                audio = np.concatenate(list(self._recent)[-(need + 4):])
                self._barge_frames = 0
                if self._gate(audio, "barge-in"):
                    log.info("barge-in: pausing reply to listen")
                    self.speaker.pause_for_interrupt()
                    self._interrupted = True
                    self.app.events.publish("voice.interrupted", soft=True)
                    self._go_active(8.0)
                    self._in_speech = True
                    self._speech = list(self._recent)[-(need + 4):]
                    self._silence = 0
                    self._voiced = need
            return

        if self.mode == self.PASSIVE and self._enroll_sink is None:
            return
        self._segment(frame, prob)

    def _track_passive(self, frame: np.ndarray, prob: float, score: float) -> None:
        """Stage 2 of wake detection: buffer each phrase heard while waiting for the wake word.

        Accents and Bluetooth hands-free audio often give a real "Hey JARVIS" a score below the
        instant-trigger threshold. Phrases whose best score passes the lower confirmation threshold
        are transcribed when they end; if Whisper hears "Jarvis" the wake word counts, and any
        command spoken in the same breath is executed directly.
        """
        if prob > 0.5:
            if not self._passive_speech:
                self._passive_audio = list(self._preroll)
            self._passive_speech += 1
            self._passive_quiet = 0
        elif self._passive_speech:
            self._passive_quiet += 1
        if self._passive_speech:
            self._passive_audio.append(frame)
        else:
            self._preroll.append(frame)
        self._passive_best = max(self._passive_best, score)
        too_long = len(self._passive_audio) * FRAME_S > 12
        if self._passive_speech and (self._passive_quiet > 18 or too_long):
            seconds = self._passive_speech * FRAME_S
            cloud = self.cfg.get("voice.wake_check", "cloud") == "cloud"
            candidate = cloud or self._passive_best >= float(self.cfg.get("voice.wake_confirm_threshold", 0.08))
            if self._passive_speech > 8 and seconds <= 7 and candidate:
                log.info("phrase heard (%.1fs, wake score %.2f) - checking for the wake word", seconds,
                         self._passive_best)
                self._speech_end = time.time()
                self._jobs.put((np.concatenate(self._passive_audio), "wake_check"))
            self._passive_speech = self._passive_quiet = 0
            self._passive_best = 0.0
            self._passive_audio = []

    def _segment(self, frame: np.ndarray, prob: float) -> None:
        if not self._in_speech:
            self._preroll.append(frame)
            if prob > 0.5:
                self._voiced += 1
                if self._voiced >= 2:
                    self._in_speech = True
                    self._speech = list(self._preroll)
                    self._silence = 0
                    self.app.events.state(State.LISTENING, "hearing you")
            else:
                self._voiced = 0
                if self._wake_enabled() and self.mode == self.ACTIVE and time.time() > self._active_deadline:
                    self._go_passive()
            return
        self._speech.append(frame)
        self._silence = self._silence + 1 if prob < 0.35 else 0
        duration = len(self._speech) * FRAME_S
        if self._silence * FRAME_S >= float(self.cfg.get("voice.end_silence", 0.6)) or duration > 25:
            audio = np.concatenate(self._speech)
            voiced_s = duration - self._silence * FRAME_S
            self._reset_segment()
            self._preroll.clear()
            if voiced_s < 0.35:
                if self._interrupted:  # a cough or click paused the reply: carry on
                    self._interrupted = False
                    self.speaker.resume_after_interrupt()
                return
            if self._enroll_sink is not None:
                self._enroll_sink.put(audio)
                return
            self._speech_end = time.time()
            self._jobs.put((audio, "utterance"))
            self.app.events.state(State.PROCESSING, "transcribing")
            if self._wake_enabled():
                self.mode = self.PASSIVE  # follow_up() re-opens after the reply

    def _gate(self, audio: np.ndarray, what: str) -> bool:
        """Real-time user recognition gate for wake-word and barge-in events."""
        mode = self.cfg.get("voice.speaker_id.mode", "enforce")
        if mode == "off" or self.recognizer is None or not self.recognizer.enrolled:
            return True
        v = self.recognizer.verify(audio)
        log.info("speaker check (%s): %s", what, v.label())
        self.app.events.publish("voice.speaker", what=what, score=round(v.score, 3), accepted=v.accepted,
                                label=v.label())
        if not v.accepted and mode == "enforce":
            log.info("ignored %s from unrecognised speaker (%.2f)", what, v.score)
            return False
        return True

    # -------------------------------------------------------- recognition
    def _process_loop(self) -> None:
        while not self._stop.is_set():
            try:
                audio, kind = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "wake_check":
                    self._confirm_wake(audio)
                else:
                    self._process(audio)
            except Exception as exc:  # noqa: BLE001
                log.exception("utterance processing failed")
                self.app.events.publish("voice.error", where="stt", error=str(exc))
                self.app.events.state(State.RECOVERING, "speech recognition failed")

    def _confirm_wake(self, audio: np.ndarray) -> None:
        if self.mode != self.PASSIVE:
            return  # already woken by the instant path
        self._save_clip(audio, "phrase")
        t = time.time()
        text, conf, engine = self.stt.transcribe(audio)
        stt_s = time.time() - t
        if not has_wake_word(text, self.wake_aliases):
            log.info("not the wake word (%s %.1fs): %r", engine, stt_s, text)
            return
        log.info("wake word confirmed (%s %.1fs): %r", engine, stt_s, text)
        if not self._gate(audio, "wake"):
            return
        self.app.events.publish("voice.wake", score=-1.0, confirmed_by="stt")
        self.interrupt()
        command = strip_wake_word(text, self.wake_aliases)
        if len(command.split()) >= 2:  # "Hey JARVIS, what time is it?" in one breath
            self.app.events.publish("voice.transcript", text=command, confidence=round(conf, 2), engine=engine,
                                    speaker="")
            self.on_utterance(command, Verification(0.0, True, False))
        else:
            self._reset_segment()
            self._go_active(8.0)

    def _save_clip(self, audio: np.ndarray, tag: str) -> None:
        """Keep the last few phrases as WAV files (data/debug/voice) for tuning recognition. Local only."""
        if not self.cfg.get("voice.save_debug_clips", True):
            return
        try:
            import wave

            folder = paths.sub("debug") / "voice"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{time.strftime('%H%M%S')}_{tag}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
            for old in sorted(folder.glob("*.wav"))[:-40]:
                old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - debugging aid only
            log.debug("could not save voice clip", exc_info=True)

    def _wake_enabled(self) -> bool:
        return self.wake is not None and bool(self.cfg.get("voice.wake_word", True))

    def _process(self, audio: np.ndarray) -> None:
        self._save_clip(audio, "command")
        mode = self.cfg.get("voice.speaker_id.mode", "enforce")
        verification = Verification(0.0, True, False)
        if self.recognizer is not None and self.recognizer.enrolled and mode != "off":
            t = time.time()
            verification = self.recognizer.verify(audio)
            self.app.events.publish("voice.speaker", what="utterance", score=round(verification.score, 3),
                                    accepted=verification.accepted, label=verification.label(),
                                    ms=int((time.time() - t) * 1000))
            if not verification.accepted and mode == "enforce":
                if self._interrupted:  # someone else talked over JARVIS: carry on
                    self._interrupted = False
                    self.speaker.resume_after_interrupt()
                self.app.events.publish("voice.rejected", score=round(verification.score, 3))
                self._go_passive()
                if not self.wake:
                    self.app.events.state(State.IDLE)
                return
        t = time.time()
        text, conf, engine = self.stt.transcribe(audio)
        log.info("heard %.1fs via %s in %.1fs: %r", audio.shape[0] / 16000, engine, time.time() - t, text)
        text = strip_wake_word(text, self.wake_aliases)
        if self._interrupted:
            self._interrupted = False
            if is_backchannel(text):
                log.info("small interruption (%r) - resuming the reply", text)
                if self.speaker.resume_after_interrupt():
                    self.app.events.publish("voice.resumed", heard=text)
                    return
            else:
                self.speaker.discard_resume()
        self.app.events.publish("voice.transcript", text=text, confidence=round(conf, 2), engine=engine,
                                speaker=verification.label())
        if not text:
            self.app.events.state(State.IDLE if self.wake else State.LISTENING)
            return
        reason = getattr(self, "_active_reason", "wake")
        if reason == "followup" and self._wake_enabled() and not looks_addressed(text):
            log.info("ignored side conversation in follow-up window: %r", text)
            self.app.events.publish("voice.ignored", text=text)
            self._go_passive()
            return
        self.on_utterance(text, verification)

    # ----------------------------------------------------------- mic check
    def mic_check(self, seconds: float = 5.0) -> dict[str, Any]:
        """Record the microphone and report whether the signal is clear enough for recognition."""
        self.interrupt()
        self._tap = []
        time.sleep(seconds)
        frames, self._tap = self._tap, None
        if not frames:
            raise RuntimeError("no audio arrived from the microphone - is it muted or disconnected?")
        a = np.concatenate(frames)
        n = len(a) // 320
        rms = np.sqrt((a[: n * 320].reshape(n, 320) ** 2).mean(axis=1))
        peak = float(np.abs(a).max())
        speech_frames = rms > max(0.01, float(np.percentile(rms, 30)) * 4)
        spec = np.abs(np.fft.rfft(a)) ** 2
        freqs = np.fft.rfftfreq(len(a), 1 / 16000)
        high = float(spec[freqs > 3500].sum() / (spec.sum() + 1e-12))
        text, _conf, engine = self.stt.transcribe(a) if speech_frames.mean() > 0.05 else ("", 0.0, "-")
        notes = []
        if peak < 0.05:
            notes.append("Very quiet: move the mic closer or raise the headset microphone level in Windows.")
        if peak > 0.98:
            notes.append("Clipping: lower the microphone level.")
        if high < 0.002:
            notes.append("Bluetooth hands-free (telephone-quality) audio: recognition works but is less "
                         "accurate than a wired/USB mic.")
        if speech_frames.mean() < 0.05:
            notes.append("No speech detected - say a sentence during the check.")
        report = {"device": self.input_device.name if self.input_device else "?", "seconds": round(len(a) / 16000, 1),
                  "peak": round(peak, 3), "speech_level": round(float(rms[speech_frames].mean()) if speech_frames.any()
                                                                else 0.0, 3),
                  "noise_floor": round(float(np.percentile(rms, 10)), 4), "wideband": high >= 0.002,
                  "heard": text, "engine": engine, "notes": notes,
                  "ok": 0.05 <= peak <= 0.98 and bool(text)}
        log.info("mic check: %s", report)
        self.app.events.publish("voice.mic_check", **report)
        return report

    def train_wake(self, samples: int = 5, timeout: float = 15.0) -> dict[str, Any]:
        """Learn how the recogniser hears this user's "Hey JARVIS" (accent/headset specific)."""
        sink: queue.Queue[np.ndarray] = queue.Queue()
        heard: list[str] = []
        self.interrupt()
        self._reset_segment()
        self._enroll_sink = sink
        try:
            for i in range(samples):
                self.app.events.publish("voice.enroll", step=i + 1, total=samples, phrase="Hey JARVIS")
                self.mode = self.ACTIVE
                self._active_deadline = time.time() + timeout
                try:
                    audio = sink.get(timeout=timeout)
                except queue.Empty as exc:
                    raise RuntimeError("didn't hear anything - check the microphone") from exc
                text, _c, _e = self.stt.transcribe(audio)
                heard.append(text)
                log.info("wake training sample %d: %r", i + 1, text)
        finally:
            self._enroll_sink = None
            self._go_passive()
        learned = sorted({re.sub(r"[^\w' ]", "", h).strip().lower() for h in heard if h.strip()} - {""})
        learned = [w for w in learned if len(w) >= 3 and len(w.split()) <= 4]
        aliases = sorted(set(self.wake_aliases) | set(learned))
        self.wake_aliases = aliases
        self.app.db.kv_set("wake_aliases", aliases)
        recognised = sum(1 for h in heard if has_wake_word(h, aliases))
        self._publish_status()
        return {"heard": heard, "learned": learned, "recognised": recognised, "samples": samples}

    def forget_wake_training(self) -> None:
        self.wake_aliases = []
        self.app.db.kv_set("wake_aliases", [])

    # ------------------------------------------------------------ enrolment
    def enroll(self, name: str, phrases: list[str] | None = None,
               on_prompt: Callable[[int, int, str], None] | None = None, timeout: float = 20.0) -> dict[str, Any]:
        """Record the user saying several phrases and store their voiceprint."""
        if self.recognizer is None:
            raise RuntimeError("speaker recognition model is unavailable")
        phrases = phrases or ENROLL_PHRASES
        sink: queue.Queue[np.ndarray] = queue.Queue()
        embeddings = []
        self.interrupt()
        self._reset_segment()
        self._enroll_sink = sink
        try:
            for i, phrase in enumerate(phrases):
                if on_prompt:
                    on_prompt(i + 1, len(phrases), phrase)
                self.app.events.publish("voice.enroll", step=i + 1, total=len(phrases), phrase=phrase)
                self.mode = self.ACTIVE
                self._active_deadline = time.time() + timeout
                try:
                    audio = sink.get(timeout=timeout)
                except queue.Empty as exc:
                    raise RuntimeError("didn't hear anything - check the microphone and try again") from exc
                if audio.shape[0] < 16000:
                    raise RuntimeError("that sample was too short; please read the whole phrase")
                embeddings.append(self.recognizer.embed(audio))
        finally:
            self._enroll_sink = None
            self._go_passive()
        vp, consistency, threshold = SpeakerRecognizer.build_voiceprint(embeddings)
        if consistency < 0.5:
            raise RuntimeError(f"samples were inconsistent ({consistency:.2f}); re-run enrolment in a quiet place")
        self.voiceprints.save(name, vp, len(embeddings), threshold)
        self.recognizer.set_voiceprint(vp, name, threshold)
        self.cfg.set("voice.speaker_id.threshold", round(threshold, 3))
        self._publish_status()
        return {"name": name, "samples": len(embeddings), "consistency": round(consistency, 3),
                "threshold": round(threshold, 3)}

    def _load_voiceprint(self) -> None:
        if self.recognizer is None:
            return
        stored = self.voiceprints.load()
        if stored:
            name, vp, threshold = stored
            threshold = float(self.cfg.get("voice.speaker_id.threshold", threshold) or threshold)
            self.recognizer.set_voiceprint(vp, name, threshold)

    def forget_voice(self) -> None:
        self.voiceprints.delete()
        if self.recognizer:
            self.recognizer.voiceprint = None
        self._publish_status()
