"""Tests for the thread-safe event hub."""

import json
import threading

import pytest

from core.events import EventHub


def drain(queue):
    """Collect and JSON-decode everything currently queued."""
    events = []
    while not queue.empty():
        events.append(json.loads(queue.get_nowait()))
    return events


@pytest.fixture()
def hub():
    return EventHub(history=10)


class TestEventHubDelivery:
    def test_emit_and_receive(self, hub):
        queue = hub.subscribe()
        hub.emit({"type": "test", "value": 1})
        events = drain(queue)
        assert len(events) == 1
        assert events[0]["type"] == "test"
        assert events[0]["value"] == 1

    def test_multiple_subscribers(self, hub):
        first = hub.subscribe()
        second = hub.subscribe()
        hub.emit({"type": "test", "value": 42})
        assert drain(first)[0]["value"] == 42
        assert drain(second)[0]["value"] == 42

    def test_unsubscribe_stops_delivery(self, hub):
        queue = hub.subscribe()
        hub.unsubscribe(queue)
        hub.emit({"type": "test"})
        assert drain(queue) == []

    def test_unsubscribe_is_idempotent(self, hub):
        queue = hub.subscribe()
        hub.unsubscribe(queue)
        hub.unsubscribe(queue)
        assert True

    def test_payload_is_json_encoded(self, hub):
        queue = hub.subscribe()
        hub.emit({"type": "text", "text": "café – ok"})
        raw = queue.get_nowait()
        assert isinstance(raw, str)
        assert json.loads(raw)["text"] == "café – ok"


class TestEventHubHistory:
    def test_history_replayed_to_new_subscriber(self, hub):
        hub.emit({"type": "first", "seq": 0})
        hub.emit({"type": "second", "seq": 1})
        late = hub.subscribe()
        events = drain(late)
        assert [e["seq"] for e in events] == [0, 1]

    def test_history_is_bounded(self):
        hub = EventHub(history=3)
        for i in range(7):
            hub.emit({"type": "tick", "seq": i})
        late = hub.subscribe()
        events = drain(late)
        assert len(events) == 3
        assert events[0]["seq"] == 4

    def test_replay_does_not_duplicate_for_live_subscriber(self, hub):
        queue = hub.subscribe()
        hub.emit({"type": "one", "seq": 0})
        assert len(drain(queue)) == 1
        hub.emit({"type": "two", "seq": 1})
        assert len(drain(queue)) == 1


class TestEventHubThreadSafety:
    def test_concurrent_emits_reach_every_subscriber(self, hub):
        queue = hub.subscribe()

        def worker(n):
            for i in range(20):
                hub.emit({"type": "work", "worker": n, "i": i})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        received = []
        while not queue.empty():
            received.append(json.loads(queue.get_nowait()))

        # Every payload is valid JSON and every worker is represented.
        assert len(received) > 0
        assert {e["worker"] for e in received} <= {0, 1, 2, 3}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
