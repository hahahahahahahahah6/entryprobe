"""Tests for wheel reading: entry_points.txt, METADATA, module resolution."""
import os
import zipfile

import pytest

from entryprobe.wheel import (
    module_file_candidates,
    module_present,
    parse_entry_points,
    read_wheel_entry_points,
    read_wheel_metadata,
    target_module,
    wheel_files,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LLAMA_WHEEL = os.path.join(FIX, "llama_cpp_scripts-0.0.0-py3-none-any.whl")


def make_wheel(tmp_path, name="demo-1.0-py3-none-any.whl", files=None, entry_points=None, metadata=""):
    files = files or {}
    path = str(tmp_path / name)
    dist_info = "demo-1.0.dist-info/"
    with zipfile.ZipFile(path, "w") as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
        if entry_points is not None:
            zf.writestr(dist_info + "entry_points.txt", entry_points)
        zf.writestr(dist_info + "METADATA", metadata or "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n")
    return path


def test_parse_entry_points_basic():
    eps = parse_entry_points("[console_scripts]\nfoo=bar:main\n")
    assert eps == {"foo": "bar:main"}


def test_parse_entry_points_no_section():
    assert parse_entry_points("[gui_scripts]\nfoo=bar:main\n") == {}


def test_parse_entry_points_empty():
    assert parse_entry_points("") == {}


def test_target_module_colon():
    assert target_module("pkg.mod:main") == "pkg.mod"


def test_target_module_extras():
    assert target_module("pkg.mod:main [extra]") == "pkg.mod"


def test_target_module_legacy_dotted():
    assert target_module("pkg.mod.main") == "pkg.mod.main"


def test_module_file_candidates_simple():
    cands = module_file_candidates("foo")
    assert "foo.py" in cands and "foo/__init__.py" in cands


def test_module_file_candidates_dotted():
    cands = module_file_candidates("a.b.c")
    assert "a/b/c.py" in cands and "a/b/__init__.py" in cands
    # legacy fallback: last segment may be the attribute
    assert "a/b.py" in cands


def test_module_present_package():
    assert module_present(["a/__init__.py", "x.py"], "a")
    assert not module_present(["a/__init__.py"], "b")


def test_module_present_top_level():
    assert module_present(["foo.py"], "foo")
    assert not module_present(["foo.py"], "bar")


def test_read_wheel_entry_points_synthetic(tmp_path):
    path = make_wheel(tmp_path, entry_points="[console_scripts]\nrunme=app:main\n")
    assert read_wheel_entry_points(path) == {"runme": "app:main"}


def test_read_wheel_entry_points_missing(tmp_path):
    path = make_wheel(tmp_path, entry_points=None)
    assert read_wheel_entry_points(path) == {}


def test_read_wheel_metadata_requires_dist(tmp_path):
    md = ("Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n"
          "Requires-Dist: requests (>=2.0)\n"
          "Requires-Dist: gguf @ file:///tmp/x/gguf-py\n")
    path = make_wheel(tmp_path, metadata=md)
    meta = read_wheel_metadata(path)
    assert "requests" in meta["requires_dist"]
    assert "gguf" in meta["requires_dist"]


def test_wheel_files_lists_all(tmp_path):
    path = make_wheel(tmp_path, files={"a.py": "x", "p/__init__.py": "y"})
    files = wheel_files(path)
    assert "a.py" in files and "p/__init__.py" in files


def test_llama_fixture_entry_points():
    eps = read_wheel_entry_points(LLAMA_WHEEL)
    assert eps["llama-convert-hf-to-gguf"] == "convert_hf_to_gguf:main"
    assert len(eps) == 4


def test_llama_fixture_has_no_conversion():
    files = wheel_files(LLAMA_WHEEL)
    assert not any(f.startswith("conversion/") for f in files)
    assert "convert_hf_to_gguf.py" in files


def test_llama_fixture_metadata():
    meta = read_wheel_metadata(LLAMA_WHEEL)
    assert "torch" in meta["requires_dist"]
    assert not any("conversion" in r for r in meta["requires_dist"])
