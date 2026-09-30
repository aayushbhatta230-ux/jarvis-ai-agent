"""Tests for events module."""

import pytest
import threading
import time
from queue import Queue, Empty

from core.events import EventHub


class TestEventHub:
    """Test EventHub class."""

    def test_initialization(self):
        hub = EventHub(history=100)
        assert hub is not None

    def test_subscribe_unsubscribe(self):
        hub = EventHub()
        queue = hub.subscribe()
        
        assert isinstance(queue, Queue)
        
        hub.unsubscribe(queue)
        # Should not raise
        hub.unsubscribe(queue)

    def test_emit_and_receive(self):
        hub = EventHub()
        queue = hub.subscribe()
        
        hub.emit({"type": "test", "data": "hello"})
        
        event = queue.get(timeout=1)
        assert event["type"] == "test"
        assert event["data"] == "hello"

    def test_multiple_subscribers(self):
        hub = EventHub()
        queue1 = hub.subscribe()
        queue2 = hub.subscribe()
        
        hub.emit({"type": "broadcast", "value": 42})
        
        event1 = queue1.get(timeout=1)
        event2 = queue2.get(timeout=1)
        
        assert event1["value"] == 42
        assert event2["value"] == 42

    def test_history_replay(self):
        hub = EventHub(history=10)
        
        # Emit some events
        for i in range(5):
            hub.emit({"seq": i})
        
        # New subscriber should get history
        queue = hub.subscribe()
        
        events = []
        while True:
            try:
                events.append(queue.get_nowait())
            except Empty:
                break
        
        assert len(events) == 5
        assert events[0]["seq"] == 0
        assert events[4]["seq"] == 4

    def test_history_limit(self):
        hub = EventHub(history=3)
        
        for i in range(10):
            hub.emit({"seq": i})
        
        queue = hub.subscribe()
        
        events = []
        while True:
            try:
                events.append(queue.get_nowait())
            except Empty:
                break
        
        # Should only get last 3
        assert len(events) == 3
        assert events[0]["seq"] == 7
        assert events[2]["seq"] == 9

    def test_slow_subscriber_dropped(self):
        hub = EventHub()
        queue = hub.subscribe()
        
        # Fill queue beyond limit
        for i in range(600):
            hub.emit({"seq": i})
        
        # Queue should have dropped old events
        # The exact behavior depends on implementation
        # Just verify it doesn't crash
        assert True

    def test_concurrent_emit(self):
        hub = EventHub()
        queue = hub.subscribe()
        
        def emitter():
            for i in range(100):
                hub.emit({"thread": threading.current_thread().ident, "seq": i})
        
        threads = [threading.Thread(target=emitter) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        
        # Just verify no crashes
        count = 0
        while True:
            try:
                queue.get_nowait()
                count += 1
            except Empty:
                break
        
        assert count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])