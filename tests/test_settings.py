"""Tests for settings module."""

import pytest
import tempfile
import json
from pathlib import Path

from core.settings import Settings, DEFAULTS, VALID_VERBOSITY, SENSITIVITY_RANGE


class TestSettings:
    """Test Settings class."""

    def test_defaults_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            settings = Settings(path)
            
            for key, default in DEFAULTS.items():
                assert settings.get(key) == default

    def test_config_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text(json.dumps({"mic_sensitivity": 1.5, "theme": "light"}))
            
            settings = Settings(path)
            assert settings.get("mic_sensitivity") == 1.5
            assert settings.get("theme") == "light"
            # Unspecified keys should use defaults
            assert settings.get("voice") == DEFAULTS["voice"]

    def test_unknown_keys_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text(json.dumps({"unknown_key": "value"}))
            
            settings = Settings(path)
            # Should not raise, unknown keys ignored
            assert settings.get("mic_sensitivity") == DEFAULTS["mic_sensitivity"]

    def test_set_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            settings = Settings(path)
            
            settings.set("mic_sensitivity", 2.0)
            assert settings.get("mic_sensitivity") == 2.0
            
            # Reload and verify persistence
            settings2 = Settings(path)
            assert settings2.get("mic_sensitivity") == 2.0

    def test_coercion_mic_sensitivity(self):
        settings = Settings()
        # Test clamping
        settings.set("mic_sensitivity", 3.0)
        assert settings.get("mic_sensitivity") == 2.5
        
        settings.set("mic_sensitivity", 0.1)
        assert settings.get("mic_sensitivity") == 0.4
        
        # Test rounding
        settings.set("mic_sensitivity", 1.234)
        assert settings.get("mic_sensitivity") == 1.23

    def test_coercion_visual_intensity(self):
        settings = Settings()
        settings.set("visual_intensity", 1.5)
        assert settings.get("visual_intensity") == 1.0
        
        settings.set("visual_intensity", -0.5)
        assert settings.get("visual_intensity") == 0.0

    def test_coercion_response_verbosity(self):
        settings = Settings()
        settings.set("response_verbosity", "invalid")
        assert settings.get("response_verbosity") == DEFAULTS["response_verbosity"]
        
        settings.set("response_verbosity", "detailed")
        assert settings.get("response_verbosity") == "detailed"

    def test_coercion_permission_level(self):
        settings = Settings()
        settings.set("permission_level", "invalid")
        assert settings.get("permission_level") == DEFAULTS["permission_level"]
        
        settings.set("permission_level", "OBSERVE_ONLY")
        assert settings.get("permission_level") == "OBSERVE_ONLY"

    def test_coercion_booleans(self):
        settings = Settings()
        settings.set("auto_listen", "true")
        assert settings.get("auto_listen") is True
        
        settings.set("auto_listen", "false")
        assert settings.get("auto_listen") is False
        
        settings.set("auto_listen", "1")
        assert settings.get("auto_listen") is True

    def test_snapshot_returns_copy(self):
        settings = Settings()
        snap = settings.snapshot()
        snap["mic_sensitivity"] = 999
        assert settings.get("mic_sensitivity") != 999

    def test_constants(self):
        assert VALID_VERBOSITY == ("concise", "normal", "detailed")
        assert SENSITIVITY_RANGE == (0.4, 2.5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])