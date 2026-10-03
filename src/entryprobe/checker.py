"""Static check: do declared console_scripts entry points resolve inside the dist?

Beyond the entry target module itself, this also parses the target's direct
imports (one level, via ast) and flags first-party modules that are missing
from the distribution. Third-party imports are recognized through
Requires-Dist (and top_level.txt when present); stdlib is always skipped.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field

from .wheel import dist_name, module_file_candidates, module_present, target_module

# Verdicts
ENTRY_OK = "ENTRY_OK"  # target module present and its direct imports resolve
ENTRY_MISSING_MODULE = "ENTRY_MISSING_MODULE"  # target or a first-party import is absent
NO_ENTRY_POINTS = "NO_ENTRY_POINTS"  # nothing declared; informational

PROBLEM_VERDICTS = {ENTRY_MISSING_MODULE}

_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _catches_import_error(try_node: ast.Try) -> bool:
    for handler in try_node.handlers:
        if handler.type is None:
            return True  # bare except
        names = set()
        if isinstance(handler.type, ast.Name):
            names.add(handler.type.id)
        elif isinstance(handler.type, ast.Tuple):
            names |= {e.id for e in handler.type.elts if isinstance(e, ast.Name)}
        if names & {"ImportError", "ModuleNotFoundError", "Exception"}:
            return True
    return False


def _is_skip_guard(node: ast.If) -> bool:
    """Guards whose bodies never run at import time: TYPE_CHECKING, __main__."""
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    # if __name__ == "__main__": — script-mode only; a console_script imports
    # the module instead, so this block never executes on entry-point start.
    if (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        names, consts = set(), set()
        for part in [test.left, *test.comparators]:
            if isinstance(part, ast.Name):
                names.add(part.id)
            elif isinstance(part, ast.Constant):
                consts.add(part.value)
        if names == {"__name__"} and consts == {"__main__"}:
            return True
    return False


def direct_import_roots(source: str, module: str) -> list[str]:
    """Module-level import roots that execute at import time (one level).

    Only top-level statements count: function/class-level (lazy) imports do
    not affect whether the entry point *starts*. Imports guarded by
    ``if TYPE_CHECKING:`` or ``try/except ImportError`` are optional and
    skipped. Unparseable source -> [] (degrades to target-presence only).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    pkg_parts = module.split(".")[:-1]
    roots: list[str] = []

    def add_from(node: ast.ImportFrom) -> None:
        if node.level and node.level > 1:
            return  # deeper relative imports: out of one-level scope
        base = pkg_parts if node.level == 1 else []
        if node.module:
            full = base + node.module.split(".")
        else:
            full = base + [a.name for a in node.names]
        if full and full[0]:
            roots.append(full[0])

    def visit_block(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Import):
                for a in stmt.names:
                    roots.append(a.name.split(".")[0])
            elif isinstance(stmt, ast.ImportFrom):
                add_from(stmt)
            elif isinstance(stmt, ast.If) and not _is_skip_guard(stmt):
                visit_block(stmt.body)
                visit_block(stmt.orelse)
            elif isinstance(stmt, ast.Try) and not _catches_import_error(stmt):
                visit_block(stmt.body)
                for handler in stmt.handlers:
                    visit_block(handler.body)
                visit_block(stmt.orelse)
                visit_block(stmt.finalbody)
            # FunctionDef/ClassDef/While/For at module level: their bodies
            # are lazy; only `if`/`try` guards are unwrapped above.

    visit_block(tree.body)
    # dedupe, preserve order
    seen: list[str] = []
    for r in roots:
        if r and r not in seen:
            seen.append(r)
    return seen


@dataclass
class EntryCheck:
    name: str
    target: str
    module: str
    verdict: str = ENTRY_OK
    detail: str = ""
    source: str = ""  # wheel path or 'installed:<name>'


def check_entry_points(
    entry_points: dict[str, str],
    files: list[str],
    *,
    read_source=None,
    requires_dist: list[str] | None = None,
    top_level: list[str] | None = None,
    source: str = "",
) -> list[EntryCheck]:
    """Check each console_scripts target.

    read_source(module) -> source text of that module inside the dist, or None.
    requires_dist: declared third-party dependency names (not flagged missing).
    """
    if not entry_points:
        return [EntryCheck(name="-", target="-", module="-",
                           verdict=NO_ENTRY_POINTS,
                           detail="no console_scripts declared",
                           source=source)]
    declared = {_normalize(dist_name(r)) for r in (requires_dist or [])}
    declared |= {_normalize(t) for t in (top_level or [])}
    results: list[EntryCheck] = []
    for name, target in entry_points.items():
        module = target_module(target)
        check = EntryCheck(name=name, target=target, module=module, source=source)
        if not module_present(files, module):
            check.verdict = ENTRY_MISSING_MODULE
            check.detail = (
                f"entry point {name!r} targets module {module!r}, "
                "which is not in the distribution"
            )
            results.append(check)
            continue
        # One-level import analysis on the entry target module.
        missing: list[str] = []
        if read_source is not None:
            src = read_source(module)
            if src is not None:
                for root in direct_import_roots(src, module):
                    if root in _STDLIB or _normalize(root) in declared:
                        continue
                    if not module_present(files, root):
                        missing.append(root)
        if missing:
            check.verdict = ENTRY_MISSING_MODULE
            check.detail = (
                f"entry point {name!r} ({module}) imports "
                + ", ".join(repr(m) for m in missing)
                + " which is not in the distribution and not a declared dependency"
            )
        results.append(check)
    return results


def has_problems(results: list[EntryCheck]) -> bool:
    return any(c.verdict in PROBLEM_VERDICTS for c in results)
