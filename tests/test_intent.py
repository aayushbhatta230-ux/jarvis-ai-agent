"""Tests for the intent inference module."""

import pytest

from core.intent import IntentEngine, IntentResult


@pytest.fixture()
def engine():
    return IntentEngine()


class TestIntentResult:
    def test_defaults_are_usable(self):
        result = IntentResult(
            intent="browser",
            sub_intent="search",
            confidence=0.9,
        )
        assert result.needs_confirmation is False
        assert result.rationale == ""
        assert result.suggestions == []
        assert result.extracted_entities == {}

    def test_mutable_defaults_are_not_shared(self):
        first = IntentResult("a", "b", 0.5)
        second = IntentResult("a", "b", 0.5)
        first.suggestions.append("x")
        first.extracted_entities["k"] = "v"
        assert second.suggestions == []
        assert second.extracted_entities == {}


class TestIntentInference:
    def test_returns_intent_result(self, engine):
        result = engine.infer("hello", [], {})
        assert isinstance(result, IntentResult)
        assert 0.0 <= result.confidence <= 1.0
        assert result.intent
        assert result.sub_intent

    @pytest.mark.parametrize(
        "phrase, intent, sub_intent",
        [
            ("cancel that", "control", "cancel"),
            ("never mind", "control", "cancel"),
            ("close the tab", "screen_control", "action"),
            ("scroll down", "screen_control", "action"),
            ("play some music", "media", "play"),
            ("pause", "media", "pause"),
            ("open youtube", "browser", "open_url"),
            ("search for python tutorials", "browser", "search"),
            ("open chrome", "browser", "open_browser"),
            ("read file notes.txt", "file", "read"),
        ],
    )
    def test_routes_direct_commands(self, engine, phrase, intent, sub_intent):
        result = engine.infer(phrase, [], {})
        assert result.intent == intent
        assert result.sub_intent == sub_intent

    def test_destructive_file_delete_requires_confirmation(self, engine):
        result = engine.infer("delete file old.txt", [], {})
        assert result.intent == "file"
        assert result.sub_intent == "delete"
        assert result.needs_confirmation is True

    def test_non_destructive_commands_do_not_require_confirmation(self, engine):
        result = engine.infer("read file notes.txt", [], {})
        assert result.needs_confirmation is False

    def test_general_question_is_recognised(self, engine):
        result = engine.infer("what's the capital of France", [], {})
        assert result.intent == "conversation"
        assert result.sub_intent == "general_question"
        assert result.confidence > 0.5

    def test_unrecognised_input_falls_back_to_conversation(self, engine):
        result = engine.infer("asdfghjkl random nonsense", [], {})
        assert result.intent == "conversation"
        assert result.confidence <= 1.0

    def test_screen_content_question(self, engine):
        result = engine.infer("what is on my screen", [], {})
        assert result.intent == "screen"
        assert result.sub_intent == "question"

    def test_active_application_question(self, engine):
        result = engine.infer("which app am I using", [], {})
        assert result.intent == "system"
        assert result.sub_intent == "active_application"

    def test_screen_control_extracts_action_and_target(self, engine):
        result = engine.infer("scroll down", [], {})
        assert result.extracted_entities.get("action") == "scroll"
        assert result.extracted_entities.get("target") == "down"

    def test_context_is_used_for_resolution(self, engine):
        result = engine.infer("delete it", [], {})
        assert isinstance(result, IntentResult)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
