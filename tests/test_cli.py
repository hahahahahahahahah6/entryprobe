"""Tests for the CLI: exit codes, formats, argument validation."""
import json
import os
import zipfile

import pytest

import entryprobe.cli as cli_mod
from entryprobe.cli import main

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LLAMA_WHEEL = os.path.join(FIX, "llama_cpp_scripts-0.0.0-py3-none-any.whl")


def make_wheel(tmp_path, entry_points="[console_scripts]\nrunme=app:main\n", files=None):
    files = files or {"app.py": "import sys\n"}
    path = str(tmp_path / "demo-1.0-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
        zf.writestr("demo-1.0.dist-info/entry_points.txt", entry_points)
        zf.writestr("demo-1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n")
    return path


def test_check_wheel_llama_fixture_exit_1(capsys):
    assert main(["check", "--wheel", LLAMA_WHEEL]) == 1
    out = capsys.readouterr().out
    assert "ENTRY_MISSING_MODULE" in out
    assert "conversion" in out


def test_check_wheel_clean_exit_0(tmp_path, capsys):
    path = make_wheel(tmp_path)
    assert main(["check", "--wheel", path]) == 0
    assert "ENTRY_OK" in capsys.readouterr().out


def test_check_wheel_missing_file(capsys):
    assert main(["check", "--wheel", "/nope.whl"]) == 2
    assert "not found" in capsys.readouterr().err


def test_check_wheel_invalid_zip(tmp_path, capsys):
    bad = tmp_path / "bad.whl"
    bad.write_text("not a zip")
    assert main(["check", "--wheel", str(bad)]) == 2


def test_check_wheel_no_entry_points(tmp_path, capsys):
    path = make_wheel(tmp_path, entry_points="[console_scripts]\n")
    assert main(["check", "--wheel", path]) == 0
    assert "no console_scripts" in capsys.readouterr().out


def test_check_package_not_installed(capsys):
    assert main(["check", "--package", "definitely-not-a-real-pkg-xyz"]) == 2
    assert "not installed" in capsys.readouterr().err


def test_check_package_installed_mocked(monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "installed_entry_points", lambda n: {"run": "app:main"})
    monkeypatch.setattr(cli_mod, "installed_files", lambda n: ["app.py"])
    monkeypatch.setattr(cli_mod, "installed_requires_dist", lambda n: [])
    assert main(["check", "--package", "whatever"]) == 0
    assert "ENTRY_OK" in capsys.readouterr().out


def test_check_json_format(tmp_path, capsys):
    path = make_wheel(tmp_path)
    assert main(["check", "--wheel", path, "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["verdict"] == "ENTRY_OK"
    assert rows[0]["name"] == "runme"


def test_smoke_multiple_entries_needs_name(tmp_path, capsys):
    path = make_wheel(tmp_path, entry_points="[console_scripts]\na=x:main\nb=y:main\n",
                      files={"x.py": "", "y.py": ""})
    assert main(["smoke", "--wheel", path, "--", "--help"]) == 2
    assert "--entry" in capsys.readouterr().err


def test_smoke_no_entry_points(tmp_path, capsys):
    path = make_wheel(tmp_path, entry_points="[console_scripts]\n")
    assert main(["smoke", "--wheel", path]) == 2


def test_smoke_bad_timeout(tmp_path, capsys):
    path = make_wheel(tmp_path)
    assert main(["smoke", "--wheel", path, "--timeout", "0"]) == 2


def test_smoke_missing_wheel(capsys):
    assert main(["smoke", "--wheel", "/nope.whl", "--", "--help"]) == 2


def test_smoke_entry_selected_and_ok(monkeypatch, tmp_path, capsys):
    path = make_wheel(tmp_path)
    from entryprobe.smoke import SmokeResult, SMOKE_OK
    monkeypatch.setattr(cli_mod, "run_smoke",
                        lambda *a, **k: SmokeResult(entry="runme", args=["--help"],
                                                   verdict=SMOKE_OK, returncode=0,
                                                   output_tail="usage"))
    assert main(["smoke", "--wheel", path, "--", "--help"]) == 0
    assert "SMOKE_OK" in capsys.readouterr().out


def test_smoke_json_format(monkeypatch, tmp_path, capsys):
    path = make_wheel(tmp_path)
    from entryprobe.smoke import SmokeResult, SMOKE_FAILED
    monkeypatch.setattr(cli_mod, "run_smoke",
                        lambda *a, **k: SmokeResult(entry="runme", args=[],
                                                   verdict=SMOKE_FAILED, returncode=1,
                                                   output_tail="boom"))
    assert main(["smoke", "--wheel", path, "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "SMOKE_FAILED"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "entryprobe" in capsys.readouterr().out
