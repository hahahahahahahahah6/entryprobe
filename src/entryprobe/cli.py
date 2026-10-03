"""entryprobe CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile

from . import __version__
from .checker import (
    NO_ENTRY_POINTS,
    EntryCheck,
    check_entry_points,
    has_problems,
)
from .installed import (
    InstalledError,
    installed_entry_points,
    installed_files,
    installed_requires_dist,
)
from .smoke import (
    SmokeError,
    SmokeResult,
    run_smoke,
    smoke_has_problems,
)
from .wheel import (
    module_file_candidates,
    read_wheel_entry_points,
    read_wheel_metadata,
    wheel_files,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="entryprobe",
        description="Verify that a package's console_scripts entry points can actually start.",
    )
    p.add_argument("--version", action="version", version="entryprobe " + __version__)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="statically check entry point targets")
    src = c.add_mutually_exclusive_group(required=True)
    src.add_argument("--wheel", default=None, help="path to a wheel file")
    src.add_argument("--package", default=None, help="name of an installed distribution")
    c.add_argument("--format", choices=["text", "json"], default="text")

    s = sub.add_parser(
        "smoke",
        help="install a LOCAL wheel into a fresh venv and run an entry point "
             "(opt-in; never downloads or runs arbitrary public packages)",
    )
    s.add_argument("--wheel", required=True, help="path to a LOCAL wheel file")
    s.add_argument("--entry", default=None,
                   help="entry point name (default: the only one, if exactly one)")
    s.add_argument("--timeout", type=int, default=120,
                   help="seconds before the run is killed (default 120)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("cmd_args", nargs=argparse.REMAINDER,
                   help="arguments for the entry point, after --")
    return p


def check_to_dict(c: EntryCheck) -> dict:
    return {"name": c.name, "target": c.target, "module": c.module,
            "verdict": c.verdict, "detail": c.detail, "source": c.source}


def format_check(results: list[EntryCheck]) -> str:
    if len(results) == 1 and results[0].verdict == NO_ENTRY_POINTS:
        return "no console_scripts declared (nothing to check)"
    rows = [("entry point", "target", "verdict")]
    for c in results:
        rows.append((c.name, c.target, c.verdict))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    problems = [c for c in results if has_problems([c])]
    lines.append("")
    lines.append(f"checked {len(results)} entry point(s), {len(problems)} problem(s)")
    for c in problems:
        if c.detail:
            lines.append(f"  {c.name}: {c.detail}")
    return "\n".join(lines)


def smoke_to_dict(r: SmokeResult) -> dict:
    return {"entry": r.entry, "args": r.args, "verdict": r.verdict,
            "returncode": r.returncode, "output_tail": r.output_tail,
            "detail": r.detail}


def format_smoke(r: SmokeResult) -> str:
    lines = [f"entry point: {r.entry} {' '.join(r.args)}".rstrip(),
             f"verdict: {r.verdict}"]
    if r.returncode is not None:
        lines.append(f"exit code: {r.returncode}")
    if r.detail:
        lines.append(r.detail)
    if r.output_tail:
        lines.append("--- output tail ---")
        lines.append(r.output_tail)
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    if args.wheel:
        path = args.wheel
        if not os.path.isfile(path):
            print(f"entryprobe: error: wheel file not found: {path}", file=sys.stderr)
            return 2
        try:
            entry_points = read_wheel_entry_points(path)
            files = wheel_files(path)
            meta = read_wheel_metadata(path)
        except zipfile.BadZipFile:
            print(f"entryprobe: error: not a valid wheel: {path}", file=sys.stderr)
            return 2
        # read_source needs the zip open during the whole check: do it inline
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            def read_source(module: str):
                for cand in module_file_candidates(module):
                    if cand in names:
                        try:
                            return zf.read(cand).decode("utf-8", "replace")
                        except Exception:
                            return None
                return None
            results = check_entry_points(
                entry_points, files, read_source=read_source,
                requires_dist=meta["requires_dist"], top_level=meta["top_level"],
                source=path,
            )
    else:
        try:
            entry_points = installed_entry_points(args.package)
            files = installed_files(args.package)
            requires = installed_requires_dist(args.package)
        except InstalledError as exc:
            print(f"entryprobe: error: {exc}", file=sys.stderr)
            return 2
        results = check_entry_points(
            entry_points, files, read_source=None,
            requires_dist=requires, source=f"installed:{args.package}",
        )
    if args.format == "json":
        print(json.dumps([check_to_dict(c) for c in results], indent=2))
    else:
        print(format_check(results))
    return 1 if has_problems(results) else 0


def cmd_smoke(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.wheel):
        print(f"entryprobe: error: wheel file not found: {args.wheel}", file=sys.stderr)
        return 2
    if args.timeout is not None and args.timeout < 1:
        print("entryprobe: error: --timeout must be >= 1", file=sys.stderr)
        return 2
    entry_points = read_wheel_entry_points(args.wheel)
    entry = args.entry
    if entry is None:
        if len(entry_points) == 1:
            entry = next(iter(entry_points))
        elif not entry_points:
            print("entryprobe: error: wheel declares no console_scripts; "
                  "use --entry is impossible", file=sys.stderr)
            return 2
        else:
            print("entryprobe: error: wheel declares multiple console_scripts; "
                  "pass --entry NAME", file=sys.stderr)
            return 2
    cmd_args = list(args.cmd_args)
    if cmd_args and cmd_args[0] == "--":
        cmd_args = cmd_args[1:]
    try:
        result = run_smoke(args.wheel, entry, cmd_args, timeout=args.timeout)
    except SmokeError as exc:
        print(f"entryprobe: error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(smoke_to_dict(result), indent=2))
    else:
        print(format_smoke(result))
    return 1 if smoke_has_problems(result) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "smoke":
        return cmd_smoke(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
