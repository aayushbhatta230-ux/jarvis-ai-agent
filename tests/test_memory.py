"""Tests for local preference and short-term memory stores."""

import json

import pytest

from core.memory import PreferenceStore, ShortTermMemory, get_short_term_memory


@pytest.fixture()
def store(tmp_path):
    return PreferenceStore(path=tmp_path / "preferences.json")


class TestPreferenceStore:
    def test_get_default(self, store):
        assert store.get("nonexistent", "fallback") == "fallback"

    def test_defaults_are_present(self, store):
        for bucket in ("user_facts", "interests", "avoidances", "behavioral_signals"):
            assert bucket in store.snapshot()

    def test_set_and_get(self, store):
        store.set("custom", {"a": 1})
        assert store.get("custom") == {"a": 1}

    def test_persistence(self, tmp_path):
        path = tmp_path / "preferences.json"
        PreferenceStore(path=path).set("custom", {"a": 1})
        assert PreferenceStore(path=path).get("custom") == {"a": 1}

    def test_snapshot_is_a_deep_copy(self, store):
        snapshot = store.snapshot()
        snapshot["user_facts"]["name"] = "Alice"
        assert "name" not in store.get("user_facts")

    def test_learns_name(self, store):
        changed = store.learn_from_text("my name is Alice")
        assert "name" in changed
        assert store.get("user_facts")["name"] == "Alice"

    def test_learns_project(self, store):
        store.learn_from_text("my project is called Jarvis")
        assert store.get("user_facts")["current_project"] == "Jarvis"

    def test_learns_interest(self, store):
        store.learn_from_text("I like programming in Python")
        assert store.get("interests")["programming"]["confidence"] > 0

    def test_learns_avoidance(self, store):
        store.learn_from_text("I don't like horror games")
        assert "gaming" in store.get("avoidances")

    def test_explicit_evidence_weighs_more(self, store):
        store.learn_from_text("I enjoy music")
        inferred = store.get("interests")["music"]["confidence"]
        store.learn_from_text("I enjoy live music", explicit=True)
        explicit = store.get("interests")["music"]["confidence"]
        assert explicit > inferred

    def test_neutral_text_changes_nothing(self, store):
        assert store.learn_from_text("what time is it") == []

    def test_record_feedback(self, store):
        before = store.get("behavioral_signals")["accepted_recommendations"]
        store.record_feedback(accepted=True)
        assert store.get("behavioral_signals")["accepted_recommendations"] == before + 1

    def test_top_interests_is_ranked(self, store):
        store.learn_from_text("I like music", explicit=True)
        store.learn_from_text("I enjoy music", explicit=True)
        ranked = store.top_interests(limit=1)
        assert ranked and ranked[0][0] == "music"

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "preferences.json"
        path.write_text("{not json", encoding="utf-8")
        assert PreferenceStore(path=path).get("user_facts") == {}

    def test_saved_file_is_valid_json(self, tmp_path):
        path = tmp_path / "preferences.json"
        PreferenceStore(path=path).set("custom", 1)
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


class TestShortTermMemory:
    def test_initialization(self):
        memory = ShortTermMemory(capacity=5)
        assert memory.capacity == 5
        assert list(memory.records) == []
        assert memory.active_draft is None

    def test_record_and_get(self):
        memory = ShortTermMemory()
        memory.record(transcript="take a screenshot", intent="screen", capability="screen.capture")
        record = memory.last()
        assert record["transcript"] == "take a screenshot"
        assert record["capability"] == "screen.capture"

    def test_records_default_to_empty_containers(self):
        memory = ShortTermMemory()
        memory.record(transcript="hello")
        record = memory.last()
        assert record["arguments"] == {}
        assert record["entities"] == {}
        assert record["extra"] == {}

    def test_capacity_is_bounded(self):
        memory = ShortTermMemory(capacity=3)
        for i in range(10):
            memory.record(transcript=f"message {i}")
        assert len(memory.records) == 3
        assert memory.last()["transcript"] == "message 9"

    def test_get_recent_limit(self):
        memory = ShortTermMemory()
        for i in range(5):
            memory.record(transcript=f"message {i}")
        assert len(list(memory.records)) == 5
        assert memory.last()["transcript"] == "message 4"

    def test_clear(self):
        memory = ShortTermMemory()
        memory.record(transcript="hello")
        memory.records.clear()
        assert memory.last() is None

    def test_last_with_kind_filter(self):
        memory = ShortTermMemory()
        memory.record(transcript="a", intent="conversation")
        memory.record(transcript="b", intent="file")
        assert memory.last(kind="file")["transcript"] == "b"
        assert memory.last(kind="missing") is None

    def test_last_action_record_skips_conversation(self):
        memory = ShortTermMemory()
        memory.record(transcript="hello", intent="conversation")
        assert memory.last_action_record() is None
        memory.record(transcript="open notepad", capability="app.open")
        assert memory.last_action_record()["transcript"] == "open notepad"

    def test_describe_last_action_without_records(self):
        assert "haven't performed" in ShortTermMemory().describe_last_action()

    def test_describe_last_action_includes_capability(self):
        memory = ShortTermMemory()
        memory.record(transcript="open notepad", capability="app.open", result="Notepad is open")
        description = memory.describe_last_action()
        assert "app.open" in description
        assert "Notepad is open" in description

    def test_describe_recent_without_records(self):
        assert "no short-term memory" in ShortTermMemory().describe_recent()

    def test_describe_recent_lists_context(self):
        memory = ShortTermMemory()
        memory.record(transcript="hello there", intent="conversation", sub_intent="general_request")
        assert "hello there" in memory.describe_recent()

    def test_resolve_action_target_for_file(self):
        memory = ShortTermMemory()
        memory.record(transcript="open notes", capability="file.read", entities={"file_reference": "notes.txt"})
        record = memory.resolve_action_target("that file")
        assert record["entities"]["file_reference"] == "notes.txt"

    def test_resolve_action_target_without_records(self):
        assert ShortTermMemory().resolve_action_target("that file") is None

    def test_active_draft_round_trip(self):
        memory = ShortTermMemory()
        memory.set_active_draft({"subject": "hi"})
        assert memory.active_draft == {"subject": "hi"}
        memory.set_active_draft(None)
        assert memory.active_draft is None

    def test_singleton_accessor(self):
        assert get_short_term_memory() is get_short_term_memory()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
