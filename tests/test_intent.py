"""Tests for intent module."""

import pytest
from unittest.mock import Mock

from core.intent import IntentEngine, Intent


class TestIntentEngine:
    """Test IntentEngine class."""

    def test_initialization(self):
        engine = IntentEngine()
        assert engine is not None
        assert hasattr(engine, 'infer')

    def test_infer_basic(self):
        engine = IntentEngine()
        context = []
        prefs = {}
        
        intent = engine.infer("hello", context, prefs)
        assert isinstance(intent, Intent)
        assert hasattr(intent, 'intent')
        assert hasattr(intent, 'sub_intent')
        assert hasattr(intent, 'confidence')
        assert hasattr(intent, 'extracted_entities')

    def test_infer_greeting(self):
        engine = IntentEngine()
        
        intent = engine.infer("hello jarvis", [], {})
        assert intent.intent in ("greeting", "chat", "general")
        assert intent.confidence > 0.5

    def test_infer_time_query(self):
        engine = IntentEngine()
        
        intent = engine.infer("what time is it", [], {})
        assert intent.intent == "time"
        assert intent.confidence > 0.7

    def test_infer_screenshot(self):
        engine = IntentEngine()
        
        intent = engine.infer("take a screenshot", [], {})
        assert intent.intent == "screen"
        assert intent.sub_intent == "screenshot"
        assert intent.confidence > 0.8

    def test_infer_volume(self):
        engine = IntentEngine()
        
        intent = engine.infer("volume up", [], {})
        assert intent.intent == "system"
        assert intent.sub_intent in ("volume_up", "volume")
        assert intent.confidence > 0.7

    def test_infer_open_app(self):
        engine = IntentEngine()
        
        intent = engine.infer("open chrome", [], {})
        assert intent.intent == "app"
        assert intent.sub_intent == "open"
        assert intent.confidence > 0.7

    def test_infer_file_read(self):
        engine = IntentEngine()
        
        intent = engine.infer("read file test.py", [], {})
        assert intent.intent == "file"
        assert intent.sub_intent == "read"
        assert intent.confidence > 0.6

    def test_infer_unknown(self):
        engine = IntentEngine()
        
        intent = engine.infer("asdfghjkl random nonsense", [], {})
        # Should still return an intent, possibly with low confidence
        assert isinstance(intent, Intent)
        assert 0 <= intent.confidence <= 1

    def test_entity_extraction(self):
        engine = IntentEngine()
        
        intent = engine.infer("open file test.py", [], {})
        entities = intent.extracted_entities or {}
        # May extract file references
        assert isinstance(entities, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])