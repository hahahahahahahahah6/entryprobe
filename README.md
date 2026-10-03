# entryprobe

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![No dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](pyproject.toml)

**Your Python package installed. Its CLI crashed on `--help`.**

Real case: [ggml-org/llama.cpp#23740](https://github.com/ggml-org/llama.cpp/issues/23740).
`uv tool install 'llama-cpp-scripts @ git+https://github.com/ggml-org/llama.cpp.git'`
succeeded — then `llama-convert-hf-to-gguf --help` died immediately:

```
ModuleNotFoundError: No module named 'conversion'
```

Root cause: since a refactor, the conversion scripts import from the new
`conversion/` package directory, but `[tool.poetry] packages` still said
`[{ include = "*.py", from = "." }]` — and a `*.py` glob does not match
directories. Every install built a wheel whose entry points pointed at code
that wasn't in the wheel. Running the scripts from the source tree worked
(Python resolved `conversion/` from the working directory), which is why
nobody noticed. Fixed by [PR #23746](https://github.com/ggml-org/llama.cpp/pull/23746)
(2026-05-27), which added `conversion` to the packaged files.

A second flavor of the same disease:
[SolaceLabs/solace-agent-mesh#1645](https://github.com/SolaceLabs/solace-agent-mesh/issues/1645) —
editable installs generate a broken `sam` console command. The entry points
target `solace_agent_mesh.cli.main:cli`, but the CLI source lives in the
top-level `cli/` directory; wheel builds force-include it via Hatch, while
editable installs expose only `src/`. (That repo is deprecated, so this one is
still broken and won't be fixed.)

entryprobe checks that a package's *declared* console_scripts entry points can
*actually start* — the release-integrity question nobody's CI asks.

Part of the release-integrity series:
[readmeta](https://github.com/hahahahahahahahah6/readmeta) (did your README
render?), [wheeltruth](https://github.com/hahahahahahahahah6/wheeltruth) (did
your wheel ship complete?), [casecrash](https://github.com/hahahahahahahahah6/casecrash)
(will your filenames survive checkout?),
[tagtruth](https://github.com/hahahahahahahahah6/tagtruth) (does the tag match
the release?), [wheelreach](https://github.com/hahahahahahahahah6/wheelreach)
(can your Python install it?), entryprobe (does the installed CLI start?).

## Quickstart

```bash
pip install entryprobe
entryprobe check --wheel dist/mypackage-1.0-py3-none-any.whl
```

```
entry point              target                  verdict
-----------------------  ----------------------  --------------------
llama-convert-hf-to-gguf convert_hf_to_gguf:main ENTRY_MISSING_MODULE

checked 1 entry point(s), 1 problem(s)
  llama-convert-hf-to-gguf: entry point 'llama-convert-hf-to-gguf' (convert_hf_to_gguf)
  imports 'conversion' which is not in the distribution and not a declared dependency
```

Or check an installed distribution:

```bash
entryprobe check --package some-installed-pkg
```

Exit codes: `0` clean, `1` problems found, `2` errors (bad args, unreadable wheel).

## What `check` actually verifies

For each `console_scripts` entry point:

1. The target module file exists in the distribution (`ENTRY_OK` vs
   `ENTRY_MISSING_MODULE`).
2. The target's **module-level imports that run at import time** resolve:
   stdlib is skipped, `Requires-Dist` dependencies are expected, and anything
   else missing from the distribution is flagged — that's the llama.cpp
   failure mode. Function-level (lazy) imports, `try/except ImportError`
   fallbacks, `if TYPE_CHECKING:` blocks, and `if __name__ == "__main__":`
   blocks don't decide whether an entry point *starts*, so they're ignored.

## Opt-in smoke test

`check` is static: it can't catch import errors *inside* a present module.
`smoke` goes one step further — on a **local wheel file you explicitly name**,
in a fresh temporary venv, running arguments you choose:

```bash
entryprobe smoke --wheel dist/mypackage-1.0-py3-none-any.whl -- --help
```

```
entry point: mycli --help
verdict: SMOKE_OK
exit code: 0
```

Safety contract: smoke never downloads or executes arbitrary public packages.
It installs with `--no-deps` into a throwaway venv (deleted afterwards) and
runs only the command you typed. Verdicts: `SMOKE_OK`, `SMOKE_FAILED`
(nonzero exit; traceback tail captured), `SMOKE_TIMEOUT`.

## Verdicts

| Verdict | Meaning | Problem? |
|---|---|---|
| `ENTRY_OK` | target module present; import-time imports resolve | no |
| `ENTRY_MISSING_MODULE` | target module — or a first-party import it needs at import time — is absent | yes |
| `NO_ENTRY_POINTS` | no `console_scripts` declared | no (informational) |
| `SMOKE_OK` / `SMOKE_FAILED` / `SMOKE_TIMEOUT` | smoke test outcome | failed/timeout: yes |

## What entryprobe does not verify

The static check is one import level deep and syntactic: it proves the entry
target and its import-time dependencies are *present*, not that importing
them *succeeds* (a module can be present and still raise). Deep runtime
failures are what `smoke` is for — and smoke only ever runs a local wheel you
name, with arguments you choose. Entry points whose targets are C extensions
or namespace packages are checked for file presence only.
