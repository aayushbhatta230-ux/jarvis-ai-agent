import pytest
from pathlib import Path
from tools.smart_files import get_recent_downloads, get_recent_user_files


def test_get_recent_downloads():
    res = get_recent_downloads(limit=5)
    assert isinstance(res, dict)
    assert "success" in res
    assert "spoken" in res
    assert "display" in res
    assert isinstance(res["files"], list)
    if res["files"]:
        assert "most_recent_path" in res
        assert Path(res["most_recent_path"]).exists()


def test_get_recent_user_files():
    res = get_recent_user_files(limit=5)
    assert isinstance(res, dict)
    assert "success" in res
    assert "spoken" in res
    assert "display" in res
    assert isinstance(res["files"], list)
    if res["files"]:
        assert "most_recent_path" in res
        assert Path(res["most_recent_path"]).exists()
