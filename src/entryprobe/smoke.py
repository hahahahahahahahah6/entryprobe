"""Opt-in smoke test: install a local wheel into a fresh venv and run an entry point.

Safety contract: smoke runs ONLY on a local wheel file the user explicitly
names, with user-specified arguments (e.g. --help, --version). It never
downloads or executes arbitrary public packages.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# Verdicts
SMOKE_OK = "SMOKE_OK"  # entry point exited 0
SMOKE_FAILED = "SMOKE_FAILED"  # nonzero exit (traceback tail captured)
SMOKE_TIMEOUT = "SMOKE_TIMEOUT"  # exceeded --timeout

SMOKE_PROBLEM_VERDICTS = {SMOKE_FAILED, SMOKE_TIMEOUT}


class SmokeError(Exception):
    """The smoke harness itself failed (venv/pip), not the entry point."""


@dataclass
class SmokeResult:
    entry: str
    args: list[str] = field(default_factory=list)
    verdict: str = SMOKE_OK
    returncode: int | None = None
    output_tail: str = ""
    detail: str = ""


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_smoke(
    wheel_path: str,
    entry: str,
    args: list[str],
    timeout: int = 120,
) -> SmokeResult:
    """Install wheel into a fresh temp venv (--no-deps) and run the entry point."""
    if not os.path.isfile(wheel_path):
        raise SmokeError(f"wheel file not found: {wheel_path}")
    tmp = tempfile.mkdtemp(prefix="entryprobe-smoke-")
    try:
        venv_dir = os.path.join(tmp, "venv")
        proc = subprocess.run(
            [sys.executable, "-m", "venv", venv_dir],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise SmokeError(f"venv creation failed: {_tail(proc.stderr)}")
        bindir = os.path.join(venv_dir, "Scripts" if os.name == "nt" else "bin")
        pip = os.path.join(bindir, "pip")
        proc = subprocess.run(
            [pip, "install", "--no-deps", "--no-input", "-q", wheel_path],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise SmokeError(f"pip install failed: {_tail(proc.stderr)}")
        script = os.path.join(bindir, entry + (".exe" if os.name == "nt" else ""))
        if not os.path.isfile(script):
            return SmokeResult(
                entry=entry, args=args, verdict=SMOKE_FAILED, returncode=None,
                detail=f"entry point script {entry!r} was not created by the install",
            )
        try:
            proc = subprocess.run(
                [script] + args,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SmokeResult(
                entry=entry, args=args, verdict=SMOKE_TIMEOUT,
                detail=f"entry point did not exit within {timeout}s",
            )
        out = _tail((proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or ""))
        if proc.returncode == 0:
            return SmokeResult(entry=entry, args=args, verdict=SMOKE_OK,
                               returncode=0, output_tail=out)
        return SmokeResult(
            entry=entry, args=args, verdict=SMOKE_FAILED,
            returncode=proc.returncode, output_tail=out,
            detail=f"entry point exited with code {proc.returncode}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def smoke_has_problems(result: SmokeResult) -> bool:
    return result.verdict in SMOKE_PROBLEM_VERDICTS
