"""Inspect installed distributions via stdlib importlib.metadata."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution

from .wheel import dist_name, parse_entry_points


class InstalledError(Exception):
    """The named package is not installed or cannot be inspected."""


def installed_entry_points(name: str) -> dict[str, str]:
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        raise InstalledError(f"package {name!r} is not installed")
    text = dist.read_text("entry_points.txt")
    if text is None:
        return {}
    return parse_entry_points(text)


def installed_files(name: str) -> list[str]:
    """File paths (relative to site-packages) recorded for an installed dist."""
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        raise InstalledError(f"package {name!r} is not installed")
    files = dist.files
    if files is None:
        raise InstalledError(
            f"package {name!r} has no recorded file list; "
            "use --wheel on a wheel file instead"
        )
    return [str(f).replace("\\", "/") for f in files]


def installed_requires_dist(name: str) -> list[str]:
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        raise InstalledError(f"package {name!r} is not installed")
    reqs = dist.metadata.get_all("Requires-Dist") or []
    return [dist_name(r) for r in reqs]
