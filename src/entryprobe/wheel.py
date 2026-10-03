"""Read entry points and file lists from wheels and installed distributions."""
from __future__ import annotations

import configparser
import zipfile


def parse_entry_points(text: str) -> dict[str, str]:
    """Parse entry_points.txt content -> {script name: 'module:attr'} for console_scripts."""
    cp = configparser.ConfigParser()
    cp.read_string(text)
    if not cp.has_section("console_scripts"):
        return {}
    return {name: target for name, target in cp.items("console_scripts")}


def read_wheel_entry_points(path: str) -> dict[str, str]:
    """{script name: target} from a wheel file; {} when no entry_points.txt."""
    with zipfile.ZipFile(path) as zf:
        matches = [n for n in zf.namelist() if n.endswith(".dist-info/entry_points.txt")]
        if not matches:
            return {}
        return parse_entry_points(zf.read(matches[0]).decode("utf-8", "replace"))


def wheel_files(path: str) -> list[str]:
    """All file names inside a wheel."""
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def dist_name(req: str) -> str:
    """'requests (>=2.0); python_version>"3.8"' -> 'requests'; 'gguf @ file:///x' -> 'gguf'."""
    return req.split(";")[0].strip().split()[0].rstrip(",")


def read_wheel_metadata(path: str) -> dict:
    """Best-effort METADATA parse -> {'requires_dist': [names], 'top_level': [modules]}."""
    out: dict = {"requires_dist": [], "top_level": []}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        md = [n for n in names if n.endswith(".dist-info/METADATA")]
        if md:
            for line in zf.read(md[0]).decode("utf-8", "replace").splitlines():
                if line.startswith("Requires-Dist:"):
                    out["requires_dist"].append(dist_name(line.split(":", 1)[1]))
        tl = [n for n in names if n.endswith(".dist-info/top_level.txt")]
        if tl:
            out["top_level"] = [
                ln.strip() for ln in zf.read(tl[0]).decode("utf-8", "replace").splitlines()
                if ln.strip()
            ]
    return out


def target_module(target: str) -> str:
    """'pkg.mod:attr' -> 'pkg.mod'; drops extras like '[extra]'."""
    return target.split("[")[0].strip().split(":")[0].strip()


def module_file_candidates(module: str) -> list[str]:
    """Possible in-wheel paths for a module.

    Covers both the full dotted path and the legacy 'module.attr' spelling
    without a colon (where the last segment may be the attribute, not a module).
    """
    parts = module.split(".")
    cands: list[str] = []
    for n in (len(parts), len(parts) - 1):
        if n >= 1:
            base = "/".join(parts[:n])
            cands.append(base + ".py")
            cands.append(base + "/__init__.py")
    return cands


def module_present(files: list[str], module: str) -> bool:
    fileset = set(files)
    return any(c in fileset for c in module_file_candidates(module))
