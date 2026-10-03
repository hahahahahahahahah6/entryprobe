"""Tests for the static checker, incl. a replay of ggml-org/llama.cpp#23740.

The fixture wheel was built from llama.cpp at pre-fix commit fda8528
(parent of the #23746 merge): its entry points target convert_hf_to_gguf,
which does `from conversion import (...)` — but conversion/ is not in the
wheel. That is exactly the reported ModuleNotFoundError.
"""
import os
import zipfile

from entryprobe.checker import (
    ENTRY_MISSING_MODULE,
    ENTRY_OK,
    NO_ENTRY_POINTS,
    UNKNOWN_IMPORT,
    check_entry_points,
    direct_import_roots,
    has_problems,
)
from entryprobe.wheel import (
    module_file_candidates,
    read_wheel_entry_points,
    read_wheel_metadata,
    wheel_files,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LLAMA_WHEEL = os.path.join(FIX, "llama_cpp_scripts-0.0.0-py3-none-any.whl")


def llama_context():
    files = wheel_files(LLAMA_WHEEL)
    eps = read_wheel_entry_points(LLAMA_WHEEL)
    meta = read_wheel_metadata(LLAMA_WHEEL)
    src_cache: dict[str, str | None] = {}
    with zipfile.ZipFile(LLAMA_WHEEL) as zf:
        names = set(zf.namelist())
        for ep_mod in {t.split(":")[0] for t in eps.values()}:
            src_cache[ep_mod] = None
            for cand in module_file_candidates(ep_mod):
                if cand in names:
                    src_cache[ep_mod] = zf.read(cand).decode("utf-8", "replace")
                    break
    return eps, files, meta, src_cache


def test_no_entry_points_is_informational():
    (c,) = check_entry_points({}, ["a.py"])
    assert c.verdict == NO_ENTRY_POINTS
    assert not has_problems([c])


def test_entry_ok_when_target_present():
    (c,) = check_entry_points({"run": "app:main"}, ["app.py"],
                              read_source=lambda m: "import sys\n")
    assert c.verdict == ENTRY_OK
    assert not has_problems([c])


def test_entry_missing_when_target_absent():
    (c,) = check_entry_points({"run": "app:main"}, ["other.py"],
                              read_source=lambda m: None)
    assert c.verdict == ENTRY_MISSING_MODULE
    assert "app" in c.detail
    assert has_problems([c])


def test_first_party_import_missing_is_unknown_not_hard_missing():
    files = ["app.py"]
    src = "import sys\nfrom helper import thing\n"
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: src)
    assert c.verdict == UNKNOWN_IMPORT
    assert "helper" in c.detail
    assert "review needed" in c.detail
    assert has_problems([c])


def test_dist_name_alias_avoids_false_positive():
    # Pillow -> PIL: declared dependency with a divergent import name.
    files = ["app.py"]
    src = "from PIL import Image\n"
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: src,
                              requires_dist=["Pillow>=9"])
    assert c.verdict == ENTRY_OK


def test_undeclared_divergent_import_is_unknown():
    # bs4 imported but beautifulsoup4 NOT declared -> needs review.
    files = ["app.py"]
    src = "from bs4 import BeautifulSoup\n"
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: src)
    assert c.verdict == UNKNOWN_IMPORT
    assert has_problems([c])


def test_declared_dependency_import_is_ok():
    files = ["app.py"]
    src = "import requests\n"
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: src,
                              requires_dist=["requests (>=2.0)"])
    assert c.verdict == ENTRY_OK


def test_stdlib_imports_are_skipped():
    files = ["app.py"]
    src = "import os, sys, argparse\nfrom pathlib import Path\n"
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: src)
    assert c.verdict == ENTRY_OK


def test_unparseable_source_degrades_gracefully():
    files = ["app.py"]
    (c,) = check_entry_points({"run": "app:main"}, files,
                              read_source=lambda m: "def broken(:\n")
    assert c.verdict == ENTRY_OK  # target presence only


def test_direct_import_roots_basic():
    roots = direct_import_roots("import os\nimport a.b\nfrom c import d\n", "m")
    assert roots == ["os", "a", "c"]


def test_direct_import_roots_relative():
    roots = direct_import_roots("from . import sib\nfrom .base import x\n", "pkg.mod")
    assert roots == ["pkg"]


def test_direct_import_roots_ignores_function_level():
    src = "import os\ndef f():\n    import lazy_dep\n"
    assert direct_import_roots(src, "m") == ["os"]


def test_direct_import_roots_skips_try_except_import_error():
    src = "try:\n    import optional_dep\nexcept ImportError:\n    optional_dep = None\n"
    assert direct_import_roots(src, "m") == []


def test_direct_import_roots_skips_type_checking():
    src = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import some_types\n"
    # `typing` itself is a real top-level import; the guarded one is skipped
    assert direct_import_roots(src, "m") == ["typing"]


def test_direct_import_roots_skips_main_guard():
    src = "import os\nif __name__ == '__main__':\n    import script_only_dep\n"
    assert direct_import_roots(src, "m") == ["os"]


def test_direct_import_roots_unparseable():
    assert direct_import_roots("def broken(:\n", "m") == []


def test_llama_fixture_replay():
    """The real #23740: entry target present, but `conversion` is missing.

    Lazy (function-level) imports like huggingface_hub must NOT be flagged:
    only what runs at import time decides whether the entry point starts.
    """
    eps, files, meta, src_cache = llama_context()
    results = check_entry_points(
        eps, files,
        read_source=lambda m: src_cache.get(m),
        requires_dist=meta["requires_dist"],
        top_level=meta["top_level"],
        source=LLAMA_WHEEL,
    )
    by_name = {c.name: c for c in results}
    conv = by_name["llama-convert-hf-to-gguf"]
    assert conv.verdict == UNKNOWN_IMPORT
    assert "conversion" in conv.detail
    assert "huggingface_hub" not in conv.detail
    assert has_problems(results)


def test_llama_fixture_torch_not_flagged():
    """torch is a declared dependency: missing from the wheel is expected."""
    eps, files, meta, src_cache = llama_context()
    results = check_entry_points(
        eps, files,
        read_source=lambda m: src_cache.get(m),
        requires_dist=meta["requires_dist"],
        top_level=meta["top_level"],
        source=LLAMA_WHEEL,
    )
    for c in results:
        assert "'torch'" not in c.detail and '"torch"' not in c.detail
