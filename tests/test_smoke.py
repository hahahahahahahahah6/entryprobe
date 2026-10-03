"""Tests for the opt-in smoke harness (mocked) + one real integration test."""
import os
import subprocess

import pytest

import entryprobe.smoke as smoke_mod
from entryprobe.smoke import (
    SMOKE_FAILED,
    SMOKE_OK,
    SMOKE_TIMEOUT,
    SmokeError,
    run_smoke,
    smoke_has_problems,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LLAMA_WHEEL = os.path.join(FIX, "llama_cpp_scripts-0.0.0-py3-none-any.whl")


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def patch_run(monkeypatch, script):
    """script: list of FakeProc returned in order, or exceptions to raise."""
    calls = {"n": 0}

    def fake(*a, **k):
        item = script[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(smoke_mod.subprocess, "run", fake)
    monkeypatch.setattr(smoke_mod.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(smoke_mod.os.path, "isfile", lambda p: True)
    return calls


def test_smoke_ok(monkeypatch):
    patch_run(monkeypatch, [FakeProc(0), FakeProc(0), FakeProc(0, stdout="usage: x")])
    r = run_smoke("f.whl", "mycli", ["--help"])
    assert r.verdict == SMOKE_OK
    assert r.returncode == 0
    assert not smoke_has_problems(r)


def test_smoke_failed_captures_tail(monkeypatch):
    err = "\n".join(f"line {i}" for i in range(30)) + "\nModuleNotFoundError: No module named 'conversion'"
    patch_run(monkeypatch, [FakeProc(0), FakeProc(0), FakeProc(1, stderr=err)])
    r = run_smoke("f.whl", "mycli", ["--help"])
    assert r.verdict == SMOKE_FAILED
    assert r.returncode == 1
    assert "ModuleNotFoundError" in r.output_tail
    assert "line 0" not in r.output_tail  # tail only
    assert smoke_has_problems(r)


def test_smoke_timeout(monkeypatch):
    patch_run(monkeypatch, [FakeProc(0), FakeProc(0),
                            subprocess.TimeoutExpired("x", 5)])
    r = run_smoke("f.whl", "mycli", [], timeout=5)
    assert r.verdict == SMOKE_TIMEOUT
    assert "5s" in r.detail
    assert smoke_has_problems(r)


def test_smoke_missing_wheel():
    with pytest.raises(SmokeError):
        run_smoke("/nonexistent/f.whl", "mycli", [])


def test_smoke_venv_failure(monkeypatch):
    patch_run(monkeypatch, [FakeProc(1, stderr="venv broke")])
    with pytest.raises(SmokeError, match="venv creation failed"):
        run_smoke("f.whl", "mycli", [])


def test_smoke_pip_failure(monkeypatch):
    patch_run(monkeypatch, [FakeProc(0), FakeProc(1, stderr="pip broke")])
    with pytest.raises(SmokeError, match="pip install failed"):
        run_smoke("f.whl", "mycli", [])


def test_smoke_script_not_created(monkeypatch):
    patch_run(monkeypatch, [FakeProc(0), FakeProc(0)])
    monkeypatch.setattr(smoke_mod.os.path, "isfile",
                        lambda p: not p.endswith("mycli"))
    r = run_smoke("f.whl", "mycli", [])
    assert r.verdict == SMOKE_FAILED
    assert "not created" in r.detail


def test_smoke_missing_wheel_file_real():
    with pytest.raises(SmokeError, match="not found"):
        run_smoke("/definitely/not/here.whl", "x", [])


@pytest.mark.slow
def test_smoke_real_llama_fixture():
    """End-to-end: install the pre-fix llama.cpp wheel, run the entry point.

    With --no-deps the first missing import is torch (a declared dep), so we
    assert the harness mechanics (nonzero exit, captured output) rather than
    the specific module name; the 'conversion' failure is covered by the
    static fixture test.
    """
    r = run_smoke(LLAMA_WHEEL, "llama-convert-hf-to-gguf", ["--help"], timeout=120)
    assert r.verdict == SMOKE_FAILED
    assert r.returncode != 0
    assert "ModuleNotFoundError" in r.output_tail
