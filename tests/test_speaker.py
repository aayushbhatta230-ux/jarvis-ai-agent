"""Tests for the Windows SAPI speaker wrapper.

The COM worker thread is stubbed out so the suite never speaks audio and
never depends on which voices happen to be installed.
"""

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from voice.speaker import Speaker, SpeakerError


@pytest.fixture()
def speaker():
    """A Speaker whose COM worker is inert but whose queue logic is real."""
    with patch.object(Speaker, "_run", lambda self: self.ready.set()):
        instance = Speaker()
    instance.engine = MagicMock()
    yield instance
    instance.stop_event.set()
    for _ in range(instance.queue.qsize()):
        instance.queue.get_nowait()


def fake_voice(descriptions):
    """A SAPI voice collection stand-in."""
    voices = MagicMock()
    voices.Count = len(descriptions)
    voices.Item.side_effect = lambda index: MagicMock(
        GetDescription=MagicMock(return_value=descriptions[index])
    )
    return voices


class TestInitialization:
    def test_exposes_the_public_api(self, speaker):
        for method in ("speak", "enqueue_chunk", "stop_speaking", "begin_utterance", "stop"):
            assert callable(getattr(speaker, method))

    def test_starts_at_generation_zero(self, speaker):
        assert speaker.generation == 0
        assert speaker.voice_name is not None

    def test_init_timeout_raises(self):
        with patch.object(Speaker, "_run", lambda self: None), patch(
            "voice.speaker.Event"
        ) as event_cls:
            event_cls.return_value.wait.return_value = False
            with pytest.raises(SpeakerError, match="timed out"):
                Speaker()

    def test_init_error_raises(self):
        def failing(self):
            self.init_error = Exception("no sapi")
            self.ready.set()

        with patch.object(Speaker, "_run", failing):
            with pytest.raises(SpeakerError):
                Speaker()


class TestVoiceSelection:
    def test_list_voices(self, speaker):
        speaker.engine.GetVoices.return_value = fake_voice(["David", "Zira"])
        assert speaker.list_voices() == ["David", "Zira"]

    def test_select_voice_prefers_male_named_voice(self, speaker):
        speaker.engine.GetVoices.return_value = fake_voice(["Microsoft Zira", "Microsoft David"])
        assert speaker._select_voice().GetDescription() == "Microsoft David"

    def test_select_voice_skips_female_voice(self, speaker):
        speaker.engine.GetVoices.return_value = fake_voice(["Microsoft Zira", "Samantha"])
        assert speaker._select_voice().GetDescription() == "Samantha"

    def test_select_voice_returns_none_without_voices(self, speaker):
        speaker.engine.GetVoices.return_value = fake_voice([])
        assert speaker._select_voice() is None


class TestQueueing:
    def test_enqueue_chunk_splits_sentences(self, speaker):
        speaker.enqueue_chunk("First sentence. Second sentence.")
        assert speaker.queue.qsize() == 2
        texts = [speaker.queue.get_nowait()[0] for _ in range(2)]
        assert texts[0].startswith("First")
        assert texts[1].startswith("Second")

    def test_enqueue_chunk_uses_current_generation(self, speaker):
        speaker.enqueue_chunk("Hello.")
        _, generation = speaker.queue.get_nowait()
        assert generation == speaker.generation

    def test_enqueue_chunk_ignores_blank_text(self, speaker):
        speaker.enqueue_chunk("")
        speaker.enqueue_chunk("   ")
        assert speaker.queue.empty()

    def test_enqueue_chunk_is_dropped_when_muted(self, speaker):
        speaker.muted = True
        speaker.enqueue_chunk("Hello.")
        assert speaker.queue.empty()

    def test_speak_does_not_bump_generation(self, speaker):
        before = speaker.generation
        speaker.speak("Hello there.", wait=False)
        assert speaker.generation == before

    def test_speak_is_a_noop_when_muted(self, speaker):
        speaker.muted = True
        speaker.speak("Hello there.", wait=False)
        assert speaker.queue.empty()


class TestInterruption:
    def test_stop_speaking_bumps_generation(self, speaker):
        before = speaker.generation
        speaker.stop_speaking()
        assert speaker.generation == before + 1

    def test_stop_speaking_purges_the_queue(self, speaker):
        speaker.enqueue_chunk("Hello.")
        speaker.stop_speaking()
        assert speaker.queue.empty()

    def test_stop_speaking_latches_cancel(self, speaker):
        speaker.stop_speaking()
        assert speaker.cancel_event.is_set()

    def test_begin_utterance_clears_the_latch(self, speaker):
        speaker.stop_speaking()
        speaker.speaking.set()
        speaker.begin_utterance()
        assert not speaker.cancel_event.is_set()
        assert not speaker.speaking.is_set()

    def test_stale_chunks_are_discarded(self, speaker):
        speaker.queue.put(("stale", speaker.generation))
        speaker.stop_speaking()
        speaker.begin_utterance()
        speaker.enqueue_chunk("Fresh.")
        _, generation = speaker.queue.get_nowait()
        assert generation == speaker.generation
        assert generation != 0

    def test_wait_until_idle_returns_when_empty(self, speaker):
        speaker.wait_until_idle(timeout=1.0)

    def test_unfinished_tracks_unacknowledged_tasks(self, speaker):
        speaker.queue = Queue()
        assert speaker.unfinished == 0
        speaker.queue.put(("a", 0))
        speaker.queue.get_nowait()
        speaker.queue.task_done()
        assert speaker.unfinished == 0

    def test_stop_is_safe_to_call_twice(self, speaker):
        speaker.stop()
        speaker.stop()


class TestSentenceChunks:
    def test_blank_input_yields_nothing(self):
        from voice.text import sentence_chunks

        assert list(sentence_chunks("   ")) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
