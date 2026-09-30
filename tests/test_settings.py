"""Tests for the persistent settings store."""

import json

import pytest

from core.settings import (
    DEFAULTS,
    SENSITIVITY_RANGE,
    VALID_VERBOSITY,
    Settings,
    _to_bool,
)


@pytest.fixture()
def path(tmp_path):
    return tmp_path / "settings.json"


@pytest.fixture()
def settings(path):
    return Settings(path)


class TestLoading:
    def test_defaults_loaded(self, settings):
        for key, default in DEFAULTS.items():
            assert settings.get(key) == default

    def test_config_file_overrides_defaults(self, path):
        path.write_text(json.dumps({"mic_sensitivity": 1.5, "theme": "light"}))
        loaded = Settings(path)
        assert loaded.get("mic_sensitivity") == 1.5
        assert loaded.get("theme") == "light"
        # Unspecified keys keep their defaults.
        assert loaded.get("voice") == DEFAULTS["voice"]
        assert loaded.get("auto_listen") == DEFAULTS["auto_listen"]

    def test_unknown_keys_ignored(self, path):
        path.write_text(json.dumps({"unknown_key": "value"}))
        loaded = Settings(path)
        assert "unknown_key" not in loaded.snapshot()
        assert loaded.get("mic_sensitivity") == DEFAULTS["mic_sensitivity"]

    def test_defaults_file_written_on_first_use(self, path):
        assert not path.exists()
        Settings(path)
        assert path.exists()
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_corrupt_file_falls_back_to_defaults(self, path):
        path.write_text("{not json", encoding="utf-8")
        assert Settings(path).get("theme") == DEFAULTS["theme"]


class TestPersistence:
    def test_set_and_save(self, settings, path):
        settings.set("mic_sensitivity", 2.0)
        assert settings.get("mic_sensitivity") == 2.0
        assert Settings(path).get("mic_sensitivity") == 2.0

    def test_set_returns_the_coerced_value(self, settings):
        assert settings.set("visual_intensity", 5.0) == 1.0

    def test_snapshot_returns_a_copy(self, settings):
        snapshot = settings.snapshot()
        snapshot["mic_sensitivity"] = 999
        assert settings.get("mic_sensitivity") != 999


class TestCoercion:
    def test_mic_sensitivity_is_clamped_and_rounded(self, settings):
        assert settings.set("mic_sensitivity", 3.0) == 2.5
        assert settings.set("mic_sensitivity", 0.1) == 0.4
        assert settings.set("mic_sensitivity", 1.234) == 1.23
        assert settings.set("mic_sensitivity", "1.5") == 1.5

    def test_mic_sensitivity_rejects_garbage(self, settings):
        assert settings.set("mic_sensitivity", "loud") == DEFAULTS["mic_sensitivity"]

    def test_visual_intensity_is_clamped(self, settings):
        assert settings.set("visual_intensity", 1.5) == 1.0
        assert settings.set("visual_intensity", -0.5) == 0.0

    def test_response_verbosity_is_validated(self, settings):
        assert settings.set("response_verbosity", "invalid") == DEFAULTS["response_verbosity"]
        assert settings.set("response_verbosity", "detailed") == "detailed"

    def test_permission_level_is_validated(self, settings):
        assert settings.set("permission_level", "invalid") == DEFAULTS["permission_level"]
        assert settings.set("permission_level", "observe_only") == "OBSERVE_ONLY"

    def test_voice_is_trimmed_and_truncated(self, settings):
        assert settings.set("voice", "  Microsoft David  ") == "Microsoft David"
        assert len(settings.set("voice", "x" * 200)) == 120

    @pytest.mark.parametrize("key", ["auto_listen", "barge_in", "pc_speaker_enabled"])
    def test_boolean_flags_coerce(self, settings, key):
        assert settings.set(key, "true") is True
        assert settings.set(key, "1") is True
        assert settings.set(key, True) is True
        assert settings.set(key, "false") is False
        assert settings.set(key, "0") is False
        assert settings.set(key, "off") is False
        assert settings.set(key, "") is False
        assert settings.set(key, False) is False


class TestToBool:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("true", True),
            ("True", True),
            (" yes ", True),
            ("1", True),
            (1, True),
            ("false", False),
            ("FALSE", False),
            ("no", False),
            ("off", False),
            ("", False),
            (0, False),
            (None, False),
        ],
    )
    def test_values(self, value, expected):
        assert _to_bool(value) is expected


class TestConstants:
    def test_valid_verbosity(self):
        assert VALID_VERBOSITY == ("concise", "normal", "detailed")

    def test_sensitivity_range(self):
        assert SENSITIVITY_RANGE == (0.4, 2.5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
