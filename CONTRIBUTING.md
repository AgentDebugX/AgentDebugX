# Contributing to AgentDebugX

Thank you for improving AgentDebugX. Contributions should preserve the public
Python API, existing CLI behavior, local-first defaults, and the separation
between Diagnose and Rerun.

## Development Setup

Create an isolated environment and install the project with development and
test dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[ui]"
python -m pip install pytest pytest-asyncio pytest-cov pytest-mock hypothesis ruff mypy
```

Python 3.9 additionally requires `tomli` for the test suite:

```bash
python -m pip install tomli
```

## Test Suites

Run the dependency-light core suite:

```bash
python -m pytest tests -q
```

Run branch coverage with the same baseline enforced by CI:

```bash
python -m pytest tests -q \
  --cov=agentdebug \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=40
```

GUI / CUA root-cause analysis is part of the main package, under
`src/agentdebug/gui`. The RCA main path imports on a core install and is
covered by `tests/test_gui_boundary.py`, so no extra setup is needed to work on
it. The rest of the tree sits behind extras: `gui-memory` for the
lesson/episodic memory layer, `gui-app` for the provider adapters, the batch
pipeline and the Streamlit annotation app.

The GUI suite lives in `tests/gui` and runs as part of `python -m pytest tests`.
On a plain install it runs the 77 tests that need nothing extra and skips the
rest through a module-level `pytest.importorskip`. To run all of it, install the
extras:

```bash
python -m pip install -e ".[gui,gui-memory,gui-app]"
python -m pytest tests/gui -q
```

CI mirrors that split: the `test` matrix covers the core-installable part on
Python 3.9-3.13, and a separate `gui-extras-tests` job installs the extras and
asserts nothing skips. When you add a GUI test, put it in `tests/gui` and guard
it with `pytest.importorskip` only if it genuinely needs an extra.

Two rules apply to anything you add under `src/agentdebug/gui`:

- Keep the code Python 3.9 compatible. Write `Optional[X]` and `Union[X, Y]`
  rather than `X | Y`; pydantic resolves these annotations at runtime, so
  `from __future__ import annotations` alone will not save you.
- Keep heavy dependencies off the core import path. `pillow` and the provider
  SDKs must be imported lazily inside a function, and no `__init__.py` reachable
  from `import agentdebug.gui` may pull in an extras-only module.

## Test Organization

- `tests/test_schema_models.py`: portable schema and serialization contracts.
- `tests/test_storage.py`: JSONL and SQLite persistence behavior.
- `tests/test_ingest.py`: offline format detection and normalization.
- `tests/test_diagnose_*.py`: Detect, Attribute, and Recover behavior.
- `tests/test_rerun.py`: planning, approval, execution, and evaluation.
- `tests/test_cli_commands.py`: CLI output, exit codes, and compatibility aliases.
- `tests/test_ui_*.py`: local UI services and FastAPI routes.
- `tests/test_hub.py`: scrubbing and bundle safety.
- `tests/test_plugins_and_compat.py`: registries, manifests, and legacy imports.
- `tests/test_llm_client.py`: mocked OpenAI-compatible transport behavior.

Tests must not call real model APIs, external services, or mutable production
resources. Use deterministic fixtures, temporary directories, and fake clients.

## Quality Checks

Before opening a pull request, run:

```bash
ruff check src/agentdebug tests
mypy \
  src/agentdebug/runtime/plugins/types.py \
  src/agentdebug/rerun/request.py \
  src/agentdebug/rerun/executors/base.py \
  src/agentdebug/diagnose/registry.py
python -m compileall -q src/agentdebug tests
```

Mypy strictly checks the stable plugin, Rerun protocol, and Diagnose registry
contracts. Add newly typed modules to this blocking baseline as their dependency
boundaries become strict. Tests, Ruff, Mypy contracts, package construction, and
the coverage baseline are blocking.

Ruff currently enforces pycodestyle, Pyflakes, comprehensions, and Bugbear. The
repository will enable import-order and pyupgrade suites in focused cleanup
changes; do not mix their full-tree mechanical rewrites into feature pull
requests.

## Pull Requests

- Keep changes focused and preserve compatibility unless a breaking change is
  explicitly approved.
- Add or update tests for every behavioral change.
- Update workflow README files when module contracts change.
- Never commit API keys, private endpoints, trace databases, generated build
  artifacts, or unsanitized user trajectories.
- Use clear commit messages and explain any compatibility or migration impact
  in the pull request description.
