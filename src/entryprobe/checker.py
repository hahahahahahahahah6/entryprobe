"""Static check: do declared console_scripts entry points resolve inside the dist?

Beyond the entry target module itself, this also parses the target's direct
imports (one level, via ast). Imports that are absent from the distribution and
don't match any declared dependency are UNKNOWN_IMPORT -- the tool cannot tell
a missing first-party module from a third-party import whose dist name differs
from its import name (beyond a small alias table), so it reports "needs review"
instead of guessing. Third-party imports are recognized through Requires-Dist
(and top_level.txt when present); stdlib is always skipped.

ENTRY_OK means "no missing imports found within static analysis scope" -- not
a proof the module imports successfully.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field

from .wheel import dist_name, module_file_candidates, module_present, target_module

# Verdicts
ENTRY_OK = "ENTRY_OK"  # target module present; no missing imports found in static scope
ENTRY_MISSING_MODULE = "ENTRY_MISSING_MODULE"  # the entry target module itself is absent
UNKNOWN_IMPORT = "UNKNOWN_IMPORT"  # an import-time dependency is unverifiable:
# absent from the dist and not matching any declared dependency -- either a
# missing first-party module or a third-party import whose dist name differs
# from its import name. Needs human review; never a silent pass.
NO_ENTRY_POINTS = "NO_ENTRY_POINTS"  # nothing declared; informational

PROBLEM_VERDICTS = {ENTRY_MISSING_MODULE, UNKNOWN_IMPORT}

# Famous distribution-name -> import-name divergences (documented heuristic).
# If an import root matches a *declared* dependency through this table, it is
# treated as declared. Anything else unverifiable -> UNKNOWN_IMPORT.
_DIST_TO_IMPORT = {
    "pillow": "pil",
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "scikit-learn": "sklearn",
    "scikit-image": "skimage",
    "python-dateutil": "dateutil",
    "pyjwt": "jwt",
    "attrs": "attr",
}

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
    # dist-name -> import-name aliases for declared deps (Pillow -> PIL, ...).
    for d in list(declared):
        alias = _DIST_TO_IMPORT.get(d)
        if alias:
            declared.add(alias)
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
        # Absent + undeclared imports are UNKNOWN_IMPORT, not a hard "missing":
        # the tool cannot tell a missing first-party module from a third-party
        # import whose dist name differs (beyond the alias table above).
        unknown: list[str] = []
        if read_source is not None:
            src = read_source(module)
            if src is not None:
                for root in direct_import_roots(src, module):
                    if root in _STDLIB or _normalize(root) in declared:
                        continue
                    if not module_present(files, root):
                        unknown.append(root)
        if unknown:
            check.verdict = UNKNOWN_IMPORT
            check.detail = (
                f"entry point {name!r} ({module}) imports "
                + ", ".join(repr(m) for m in unknown)
                + ": not in the distribution and not matching any declared "
                + "dependency -- either a missing first-party module or a "
                + "third-party import whose distribution name differs from its "
                + "import name (e.g. Pillow -> PIL); review needed"
            )
        results.append(check)
    return results


def has_problems(results: list[EntryCheck]) -> bool:
    return any(c.verdict in PROBLEM_VERDICTS for c in results)
