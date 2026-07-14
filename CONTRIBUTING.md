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

The CUA debugger is an independent optional package with Python 3.10+ and a
heavier dependency set:

```bash
python -m pip install -e ./cua_debugger
python -m pytest cua_debugger/tests -q
```

Do not add CUA tests to the root `testpaths`; CI runs them in an isolated job
so the AgentDebugX core installation remains lightweight.

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
