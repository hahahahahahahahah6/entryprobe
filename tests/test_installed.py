"""Tests for installed-distribution inspection."""
import pytest

import entryprobe.installed as inst_mod
from entryprobe.installed import (
    InstalledError,
    installed_entry_points,
    installed_files,
    installed_requires_dist,
)


def test_not_installed_entry_points():
    with pytest.raises(InstalledError, match="not installed"):
        installed_entry_points("definitely-not-a-real-pkg-xyz")


def test_not_installed_files():
    with pytest.raises(InstalledError, match="not installed"):
        installed_files("definitely-not-a-real-pkg-xyz")


def test_installed_entry_points_mocked(monkeypatch):
    class FakeDist:
        def read_text(self, name):
            assert name == "entry_points.txt"
            return "[console_scripts]\nrunme=app:main\n"

    monkeypatch.setattr(inst_mod, "distribution", lambda n: FakeDist())
    assert installed_entry_points("whatever") == {"runme": "app:main"}


def test_installed_entry_points_missing_file_mocked(monkeypatch):
    class FakeDist:
        def read_text(self, name):
            return None

    monkeypatch.setattr(inst_mod, "distribution", lambda n: FakeDist())
    assert installed_entry_points("whatever") == {}


def test_installed_files_mocked(monkeypatch):
    class FakePath:
        def __init__(self, s): self._s = s
        def __str__(self): return self._s

    class FakeDist:
        files = [FakePath("app.py"), FakePath("pkg/__init__.py")]

    monkeypatch.setattr(inst_mod, "distribution", lambda n: FakeDist())
    assert installed_files("whatever") == ["app.py", "pkg/__init__.py"]


def test_installed_files_none_raises(monkeypatch):
    class FakeDist:
        files = None

    monkeypatch.setattr(inst_mod, "distribution", lambda n: FakeDist())
    with pytest.raises(InstalledError, match="no recorded file list"):
        installed_files("whatever")


def test_installed_requires_dist_mocked(monkeypatch):
    class FakeMeta:
        def get_all(self, name):
            return ["requests (>=2.0); python_version >= '3.8'", "numpy"]

    class FakeDist:
        metadata = FakeMeta()

    monkeypatch.setattr(inst_mod, "distribution", lambda n: FakeDist())
    assert installed_requires_dist("whatever") == ["requests", "numpy"]
