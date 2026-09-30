"""Tests for the persistent conversation history store."""

import json

import pytest

from core.history import HistoryStore


@pytest.fixture()
def path(tmp_path):
    return tmp_path / "history.json"


class TestHistoryStore:
    def test_missing_file_loads_empty(self, path):
        store = HistoryStore(path=path)
        assert store.snapshot() == []

    def test_append_and_get(self, path):
        store = HistoryStore(path=path)
        store.append("user", "hello")
        store.append("assistant", "hi there")
        entries = store.snapshot()
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[0]["text"] == "hello"
        assert entries[1]["role"] == "assistant"

    def test_append_records_timestamp(self, path):
        store = HistoryStore(path=path)
        store.append("user", "hello")
        assert store.snapshot()[0]["ts"]

    def test_get_with_limit_via_store_limit(self, path):
        store = HistoryStore(path=path, limit=3)
        for i in range(6):
            store.append("user", f"message {i}")
        entries = store.snapshot()
        assert len(entries) == 3
        assert entries[-1]["text"] == "message 5"

    def test_snapshot_is_a_copy(self, path):
        store = HistoryStore(path=path)
        store.append("user", "hello")
        snapshot = store.snapshot()
        snapshot.append({"role": "user", "text": "injected"})
        assert len(store.snapshot()) == 1

    def test_clear(self, path):
        store = HistoryStore(path=path)
        store.append("user", "hello")
        store.clear()
        assert store.snapshot() == []
        assert HistoryStore(path=path).snapshot() == []

    def test_persistence(self, path):
        first = HistoryStore(path=path)
        first.append("user", "remember this")
        second = HistoryStore(path=path)
        assert second.snapshot()[0]["text"] == "remember this"

    def test_persistence_writes_a_json_array(self, path):
        store = HistoryStore(path=path)
        store.append("user", "hello")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert data[0]["role"] == "user"

    def test_corrupt_file_is_ignored(self, path):
        path.write_text("{not json", encoding="utf-8")
        store = HistoryStore(path=path)
        assert store.snapshot() == []

    def test_non_array_payload_is_ignored(self, path):
        path.write_text('{"role": "user"}', encoding="utf-8")
        store = HistoryStore(path=path)
        assert store.snapshot() == []

    def test_entries_with_unknown_roles_are_dropped(self, path):
        path.write_text(
            json.dumps(
                [
                    {"role": "system", "text": "ignored"},
                    {"role": "user", "text": "kept"},
                ]
            ),
            encoding="utf-8",
        )
        store = HistoryStore(path=path)
        assert [e["text"] for e in store.snapshot()] == ["kept"]

    def test_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "history.json"
        store = HistoryStore(path=nested)
        store.append("user", "hello")
        assert nested.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
