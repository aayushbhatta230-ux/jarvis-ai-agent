"""Tests for the deterministic action planner router."""

import pytest

from core.intent import IntentEngine, IntentResult
from core.planner import ActionPlanner


@pytest.fixture()
def planner():
    return ActionPlanner(IntentEngine())


class TestActionPlannerRouting:
    def test_initialization(self, planner):
        assert planner is not None
        assert hasattr(planner, "execute")
        assert hasattr(planner, "needs_confirmation")

    def test_execute_uses_supplied_intent(self, planner):
        intent = IntentResult("conversation", "general_request", 0.4)
        # A conversational intent has no deterministic handler.
        assert planner.execute("random nonsense", intent) is None

    @pytest.mark.parametrize("transcript", ["", "   ", "\n\t"])
    def test_blank_transcripts_are_ignored(self, planner, transcript):
        assert planner.execute(transcript) is None

    @pytest.mark.parametrize(
        "intent, sub_intent, handler_name",
        [
            ("system", "capabilities", "_handle_system_capabilities"),
            ("system", "active_application", "_handle_active_application"),
            ("screen", "question", "_handle_screen_question"),
            ("screen_control", "action", "_handle_screen_control"),
            ("media", "play", "_handle_media_play"),
            ("media", "pause", "_handle_media_pause"),
            ("browser", "open_url", "_handle_browser_open"),
            ("browser", "search", "_handle_browser_search"),
            ("file", "read", "_handle_file_read"),
            ("file", "list", "_handle_file_list"),
            ("app", "open", "_handle_app_open"),
        ],
    )
    def test_handler_registry(self, planner, intent, sub_intent, handler_name):
        handler = planner._get_handler(intent, sub_intent)
        assert handler is not None, f"{intent}/{sub_intent} should be routable"
        assert handler.__name__ == handler_name

    def test_unknown_intent_has_no_handler(self, planner):
        assert planner._get_handler("nope", "nope") is None

    def test_unknown_capability_needs_no_confirmation(self, planner):
        assert planner.needs_confirmation("definitely_not_a_capability") is False


class TestWebSearchGuard:
    def test_local_intent_blocks_web_search_fallback(self, planner):
        intent = IntentResult("file", "read", 0.9)
        planner.execute("read file notes.txt", intent)
        assert planner._web_search_blocked is True

    def test_unknown_capability_returns_false(self, planner):
        assert planner.needs_confirmation("definitely_not_a_capability") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
