"""Tests for planner module."""

import pytest
from unittest.mock import Mock

from core.planner import ActionPlanner
from core.intent import IntentEngine, Intent


class TestActionPlanner:
    """Test ActionPlanner class."""

    def test_initialization(self):
        engine = IntentEngine()
        planner = ActionPlanner(engine)
        assert planner is not None
        assert hasattr(planner, 'execute')

    def test_execute_known_intent(self):
        engine = IntentEngine()
        planner = ActionPlanner(engine)
        
        intent = Intent(
            intent="screen",
            sub_intent="screenshot",
            confidence=0.9,
            extracted_entities={}
        )
        
        result = planner.execute("take a screenshot", intent)
        assert result is not None
        assert hasattr(result, 'success')
        assert hasattr(result, 'tool')
        assert hasattr(result, 'result')

    def test_execute_unknown_intent(self):
        engine = IntentEngine()
        planner = ActionPlanner(engine)
        
        intent = Intent(
            intent="unknown",
            sub_intent="unknown",
            confidence=0.1,
            extracted_entities={}
        )
        
        result = planner.execute("random nonsense", intent)
        # Should return None for unknown intents
        assert result is None

    def test_execute_with_entities(self):
        engine = IntentEngine()
        planner = ActionPlanner(engine)
        
        intent = Intent(
            intent="file",
            sub_intent="read",
            confidence=0.8,
            extracted_entities={"file_reference": "test.py"}
        )
        
        result = planner.execute("read test.py", intent)
        assert result is not None

    def test_execute_confirmation_required(self):
        engine = IntentEngine()
        planner = ActionPlanner(engine)
        
        intent = Intent(
            intent="file",
            sub_intent="delete",
            confidence=0.9,
            extracted_entities={"file_reference": "test.py"}
        )
        
        result = planner.execute("delete test.py", intent)
        # Delete might require confirmation
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])