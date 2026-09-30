"""Tests for the local-model brain wrapper.

Ollama and the OpenJarvis engine are stubbed, so the suite never needs a
running model service.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.brain import Brain, BrainError


@pytest.fixture()
def brain():
    with patch("core.brain.Jarvis"), patch("core.ollama_helper.ensure_ollama_running"):
        instance = Brain()
    jarvis = MagicMock()
    # Default: the direct engine path fails so the ask() fallback runs.
    jarvis._engine.generate.side_effect = RuntimeError("engine unavailable")
    jarvis._engine.stream.side_effect = RuntimeError("engine unavailable")
    jarvis.ask.return_value = "Test response"
    instance._jarvis = jarvis
    return instance


class TestInitialization:
    def test_exposes_the_public_api(self, brain):
        assert hasattr(brain, "model")
        assert hasattr(brain, "respond")
        assert hasattr(brain, "respond_stream")
        assert hasattr(brain, "respond_fast")
        assert hasattr(brain, "warm_up")

    def test_default_model(self, brain):
        assert brain.model == "llama3.2:latest"

    def test_starts_with_empty_history(self, brain):
        assert brain.history == []


class TestScreenDependencyDetection:
    @pytest.mark.parametrize(
        "prompt",
        [
            "what is on my screen",
            "click on File",
            "what tabs are open",
            "ocr the screen",
            "which browser am I using",
        ],
    )
    def test_screen_dependent(self, brain, prompt):
        assert brain._is_screen_dependent(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        ["hello", "what is the capital of France", "write a poem about rain"],
    )
    def test_not_screen_dependent(self, brain, prompt):
        assert brain._is_screen_dependent(prompt) is False


class TestRespond:
    def test_returns_a_string(self, brain):
        response = brain.respond("Hello")
        assert isinstance(response, str)
        assert response == "Test response"

    def test_prefers_the_direct_engine_path(self, brain):
        brain._jarvis._engine.generate.side_effect = None
        brain._jarvis._engine.generate.return_value = {"content": "  From the engine  "}
        assert brain.respond("Hello") == "From the engine"

    def test_falls_back_to_ask(self, brain):
        brain.respond("Hello")
        brain._jarvis.ask.assert_called_once()

    def test_history_records_the_turn(self, brain):
        brain.respond("Hello")
        assert brain.history[0] == {"role": "user", "content": "Hello"}
        assert brain.history[1]["role"] == "assistant"

    def test_wraps_failures_in_brain_error(self, brain):
        brain._jarvis._engine.generate.side_effect = RuntimeError("engine down")
        brain._jarvis.ask.side_effect = Exception("ask failed")
        with pytest.raises(BrainError, match="unavailable"):
            brain.respond("Hello")

    def test_empty_response_raises(self, brain):
        brain._jarvis.ask.return_value = "   "
        with pytest.raises(BrainError, match="empty response"):
            brain.respond("Hello")


class TestRespondStream:
    def test_yields_chunks(self, brain):
        async def tokens(*args, **kwargs):
            for token in ("Hello", " world", "!"):
                yield token

        brain._jarvis._engine.stream.side_effect = None
        brain._jarvis._engine.stream = tokens
        assert list(brain.respond_stream("Hello")) == ["Hello", " world", "!"]

    def test_empty_stream_raises(self, brain):
        async def nothing(*args, **kwargs):
            return
            yield  # pragma: no cover

        brain._jarvis._engine.stream = nothing
        with pytest.raises(BrainError, match="empty response"):
            list(brain.respond_stream("Hello"))


class TestWarmUp:
    def test_warm_up_reports_success(self, brain):
        with patch("urllib.request.urlopen") as urlopen, patch.object(
            brain, "start_keep_alive_heartbeat"
        ):
            urlopen.return_value.__enter__.return_value.read.return_value = b""
            assert brain.warm_up() is True

    def test_warm_up_reports_failure_without_raising(self, brain):
        with patch("urllib.request.urlopen", side_effect=OSError("no ollama")):
            assert brain.warm_up() is False


class TestErrorType:
    def test_brain_error_is_a_runtime_error(self):
        assert issubclass(BrainError, RuntimeError)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
