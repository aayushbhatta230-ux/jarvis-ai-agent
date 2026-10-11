"""Voice pipeline: device selection, text handling, speaker ID, state transitions (no real audio I/O)."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pytest

from jarvis.core import paths
from jarvis.voice import devices
from jarvis.voice.speaker_id import SpeakerRecognizer, Verification, fbank
from jarvis.voice.stt import clean_transcript, strip_wake_word
from jarvis.voice.tts import Speaker, resample, speakable


@pytest.mark.parametrize("name,expect_better_than", [
    ("Headset (YS-511)", "Microphone (Web Camera)"),
    ("Headset Microphone (Realtek(R) Audio)", "Microphone Array (Intel® Smart Sound Technology)"),
    ("Microphone (Web Camera)", "Microphone Array (Intel® Smart Sound Technology for Digital Microphones)"),
    ("Microphone (USB Audio)", "Microphone (Web Camera)"),
])
def test_input_scoring_prefers_headset_and_avoids_builtin(name, expect_better_than):
    assert devices.score_input(name) > devices.score_input(expect_better_than)


def test_output_scoring_prefers_headphones():
    assert devices.score_output("Headphones (YS-511)") > devices.score_output("Speaker (Realtek(R) Audio)")
    assert devices.score_output("Speaker (Realtek(R) Audio)") > devices.score_output("NVIDIA Output (HDMI)")


def test_choose_input_respects_config_and_auto(monkeypatch):
    fake = [devices.Device(1, "Microphone Array (Intel® Smart Sound)", "Windows WASAPI", 2, 48000),
            devices.Device(2, "Microphone (Web Camera)", "Windows WASAPI", 1, 48000),
            devices.Device(3, "Headset (YS-511)", "Windows WASAPI", 1, 16000)]
    monkeypatch.setattr(devices, "list_devices", lambda kind: [devices.Device(**d.__dict__) for d in fake])
    assert devices.choose_input().name == "Headset (YS-511)"
    assert devices.choose_input("web camera").index == 2
    assert devices.choose_input("missing device").name == "Headset (YS-511)"
    fake.pop()  # headset unplugged
    assert devices.choose_input().name == "Microphone (Web Camera)"


def test_transcript_cleanup():
    assert strip_wake_word("Hey Jarvis, open notepad") == "open notepad"
    assert strip_wake_word("jarvis what's the time") == "what's the time"
    assert strip_wake_word("open jarvis folder") == "open jarvis folder"
    assert clean_transcript(" Thank you. ") == ""
    assert clean_transcript("Open   Chrome") == "Open Chrome"


def test_speakable_and_resample():
    s = speakable("**Done.** See [docs](https://x.y) and run `pytest`.\n```py\nprint(1)\n```")
    assert "**" not in s and "https" not in s and "print(1)" not in s and "pytest" in s
    x = np.sin(np.linspace(0, 100, 24000)).astype(np.float32)
    assert resample(x, 24000, 48000).shape[0] == 48000


class _NullPlayer:
    def __init__(self):
        self.idle = threading.Event()
        self.idle.set()
        self.written = []
        self.volume = 1.0

    def write(self, pcm, rate):
        self.written.append(len(pcm))

    def stop(self):
        self.written.clear()
        self.idle.set()

    def close(self):
        pass


def test_speaker_streams_sentences_and_stops(monkeypatch):
    sp = Speaker(backend="none")
    sp.player = _NullPlayer()
    synthesized = []

    def fake_synth(text):
        synthesized.append(text)
        time.sleep(0.05)
        return np.zeros(100, dtype=np.float32), 16000

    monkeypatch.setattr(sp, "_synth", fake_synth)
    for piece in ["Hello there. ", "This is a ", "streamed reply! And", " a tail"]:
        sp.feed(piece)
    time.sleep(0.3)
    assert synthesized[:2] == ["Hello there.", "This is a streamed reply!"]  # spoken before the reply finished
    sp.flush()
    time.sleep(0.3)
    assert synthesized[-1] == "And a tail"
    for _ in range(10):
        sp.feed("Sentence number one. ")
    sp.stop()
    time.sleep(0.3)
    assert len(synthesized) < 8  # queued sentences were dropped on interruption


def _models() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "models"


@pytest.mark.skipif(not (_models() / "wespeaker_en_voxceleb_resnet34.onnx").exists(), reason="speaker model missing")
def test_speaker_recognition_separates_voices():
    rec = SpeakerRecognizer(_models())
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 3) / 16000

    def voice(f0, seed):
        r = np.random.default_rng(seed)
        sig = sum(np.sin(2 * np.pi * f0 * k * t + r.uniform(0, 6)) / k for k in range(1, 12))
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t + r.uniform(0, 6))
        return (0.1 * sig * env + 0.002 * rng.standard_normal(t.shape)).astype(np.float32)

    a = [rec.embed(voice(110, s)) for s in range(3)]
    vp, consistency, threshold = SpeakerRecognizer.build_voiceprint(a)
    rec.set_voiceprint(vp, "me", threshold)
    assert fbank(voice(110, 9)).shape[1] == 80
    same = rec.verify(voice(110, 7))
    other = rec.verify(voice(240, 7))
    assert same.score > other.score


def _bare_voice_manager(app):
    """A VoiceManager with no hardware: we drive _handle() with synthetic frames."""
    from jarvis.voice.pipeline import VoiceManager

    vm = VoiceManager.__new__(VoiceManager)
    vm.app, vm.cfg = app, app.config
    vm.on_utterance = lambda text, v: None
    vm.mode = VoiceManager.PASSIVE
    vm._active_deadline = 0.0
    vm._in_speech = False
    vm._speech, vm._silence, vm._voiced = [], 0, 0
    vm._preroll, vm._recent = deque(maxlen=10), deque(maxlen=60)
    vm._wake_refractory = 0.0
    vm._barge_frames = 0
    vm._passive_speech = vm._passive_quiet = 0
    vm._passive_best = 0.0
    vm._passive_audio = []
    vm.wake_aliases = []
    vm._tap = None
    vm._speech_end = 0.0
    vm._jobs = queue.Queue()
    vm._enroll_sink = None
    vm.muted = False
    vm.recognizer = None
    vm.speaker = type("S", (), {"speaking": False, "stop": lambda self: None})()
    class _Vad:
        def __call__(self, f):
            return float(np.abs(f).mean() > 0.05)

        def reset(self):
            pass

    vm.vad = _Vad()
    vm.wake = None
    return vm


def test_voice_state_transitions_wake_utterance_bargein(app):
    from jarvis.voice.pipeline import VoiceManager

    vm = _bare_voice_manager(app)
    states = []
    app.events.subscribe("assistant.state", lambda e: states.append(e.data["state"]))

    class FakeWake:
        def __init__(self):
            self.fire = False

        def process(self, f):
            return 0.99 if self.fire else 0.0

    vm.wake = FakeWake()
    silence = np.zeros(512, dtype=np.float32)
    speech = np.full(512, 0.2, dtype=np.float32)
    vm._handle(silence)
    assert vm.mode == VoiceManager.PASSIVE
    vm.wake.fire = True
    vm._handle(silence)
    vm.wake.fire = False
    assert vm.mode == VoiceManager.ACTIVE and "listening" in states
    for _ in range(30):
        vm._handle(speech)
    for _ in range(30):
        vm._handle(silence)
    audio, _ = vm._jobs.get_nowait()
    assert audio.shape[0] >= 30 * 512 and "processing" in states
    # barge-in while speaking
    stopped = []
    vm.speaker = type("S", (), {"speaking": True, "stop": lambda self: stopped.append(1)})()
    vm.interrupt = lambda: (stopped.append(1), setattr(vm.speaker, "speaking", False))
    for _ in range(9):
        vm._handle(speech)
    assert stopped and vm.mode == VoiceManager.ACTIVE and vm._in_speech


def test_voice_gate_enforces_enrolled_speaker(app):
    vm = _bare_voice_manager(app)

    class Rec:
        enrolled = True

        def verify(self, audio):
            return Verification(0.2, False, True, "me")

    vm.recognizer = Rec()
    app.config.set("voice.speaker_id.mode", "enforce")
    assert vm._gate(np.zeros(16000, dtype=np.float32), "wake") is False
    app.config.set("voice.speaker_id.mode", "tag")
    assert vm._gate(np.zeros(16000, dtype=np.float32), "wake") is True


def test_rejected_speaker_never_reaches_assistant(app):
    vm = _bare_voice_manager(app)
    heard = []
    vm.on_utterance = lambda text, v: heard.append(text)

    class Rec:
        enrolled = True

        def verify(self, audio):
            return Verification(0.1, False, True, "me")

    class STT:
        def transcribe(self, audio, **kw):
            return "delete everything", 0.9, "fake"

    vm.recognizer, vm.stt = Rec(), STT()
    app.config.set("voice.speaker_id.mode", "enforce")
    rejected = []
    app.events.subscribe("voice.rejected", lambda e: rejected.append(e))
    vm._process(np.zeros(16000, dtype=np.float32))
    assert heard == [] and rejected
    app.config.set("voice.speaker_id.mode", "off")
    vm._process(np.zeros(16000, dtype=np.float32))
    assert heard == ["delete everything"]


def test_paths_use_project_data(home):
    assert paths.data_home() == home


def test_wake_word_matching_tolerates_mishearings():
    from jarvis.voice.stt import has_wake_word

    for heard in ("Hey Jarvis", "We're service, what time is it?", "hey jervis open chrome", "Hi Javis."):
        assert has_wake_word(heard), heard
    assert not has_wake_word("what's the weather like today")
    assert not has_wake_word("his service was good")
    assert strip_wake_word("We're service, what time is it?") == "what time is it?"


def test_borderline_wake_is_confirmed_by_speech_recognition(app):
    from jarvis.voice.pipeline import VoiceManager

    vm = _bare_voice_manager(app)
    heard = []
    vm.on_utterance = lambda text, v: heard.append(text)

    class STT:
        def __init__(self, text):
            self.text = text

        def transcribe(self, audio, **kw):
            return self.text, 0.8, "fake"

    vm.stt = STT("Hey Jarvis, what time is it?")
    vm._confirm_wake(np.zeros(16000, dtype=np.float32))
    assert heard == ["what time is it?"]          # wake + command in one breath
    vm.mode = VoiceManager.PASSIVE
    vm.stt = STT("Hey Jarvis.")
    vm._confirm_wake(np.zeros(16000, dtype=np.float32))
    assert vm.mode == VoiceManager.ACTIVE          # wake only -> listen for the command
    vm.mode = VoiceManager.PASSIVE
    vm.stt = STT("the weather is nice")
    vm._confirm_wake(np.zeros(16000, dtype=np.float32))
    assert vm.mode == VoiceManager.PASSIVE and len(heard) == 1


def test_passive_phrase_with_borderline_score_is_queued(app):
    vm = _bare_voice_manager(app)
    for _ in range(20):
        vm._track_passive(np.full(512, 0.2, np.float32), 0.9, 0.3)
    for _ in range(20):
        vm._track_passive(np.zeros(512, np.float32), 0.0, 0.0)
    audio, kind = vm._jobs.get_nowait()
    assert kind == "wake_check" and audio.shape[0] >= 20 * 512


def test_learned_wake_aliases_match_users_pronunciation():
    from jarvis.voice.stt import has_wake_word

    aliases = ["rajasthani", "jay vishay"]
    assert has_wake_word("Rajasthani close Visual Studio", aliases)
    assert strip_wake_word("Rajasthani close Visual Studio", aliases) == "close Visual Studio"
    assert has_wake_word("jay vishay mein audible to you", aliases)
    assert not has_wake_word("Rajasthani close Visual Studio")  # only once learned
    assert not has_wake_word("open the file please", aliases)


def test_transcript_junk_is_dropped():
    assert clean_transcript("VS Code, PowerShell, PowerShell, PowerShell, PowerShell, PowerShell") == ""
    assert clean_transcript("We'll see you in the next video.") == ""
    assert clean_transcript("can you close vs code") == "can you close vs code"
