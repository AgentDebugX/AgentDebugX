# dsh-agentdebugx

A separately packaged DeepSeek Harness plugin kept under the AgentDebugX
repository. It connects Harness sessions to AgentDebugX without coupling
Harness-specific code into the `agentdebug` Python package.

The plugin:

- captures each completed Harness turn into AgentDebugX `AgentTrajectory`;
- stores trajectories in AgentDebugX SQLite storage;
- exposes `/agentdebug status|capabilities|diagnose|open`;
- exposes model-facing session diagnosis, saved-trace analysis, and
  capability-discovery tools;
- reads Harness's own persisted sessions, including the concatenated-Zstandard
  `session.jsonl.zstd` container;
- opens the AgentDebugX viewer on the turn that just finished;
- records replayable `agentdebug/start` and `agentdebug/result` session events;
- keeps all Harness-specific code isolated under `integrations/dsh-agentdebugx`.

## Status and compatibility

This first integration targets DeepSeek Harness `0.1.0-rc.7` and AgentDebugX
`0.3.x`. Harness is in developer preview and may introduce breaking changes.

The bridge exposes AgentDebugX's deterministic heuristic pipeline and the
DeepDebug profile. The external protocol deliberately leaves room for GUI RCA,
discussion, and rerun operations without requiring those host-specific concerns
to enter AgentDebugX core.

## Install from a local checkout

Prerequisites:

- Node.js 22.19+ or 24+;
- a Node-installed DeepSeek Harness profile;
- Python 3.9+;
- AgentDebugX with the optional dashboard dependencies.

```powershell
cd C:\path\to\AgentDebugX
python -m pip install -e ".[ui]"
dsh plugin --profile web add C:\path\to\AgentDebugX\integrations\dsh-agentdebugx
dsh --profile web --dump-config
dsh web
```

When running Harness from its source checkout, prefix the DSH commands with
`pnpm`:

```powershell
pnpm dsh plugin --profile web add C:\path\to\AgentDebugX\integrations\dsh-agentdebugx
pnpm dsh --profile web --dump-config
pnpm dsh web
```

The plugin does not use `deepseek-harness-sdk`'s bundled Python runtime. The
Node Harness host starts the packaged bridge script with the configured local
Python interpreter, which also works on Windows.

## Updating with AgentDebugX

For local development, both sides are linked rather than copied:

- `pip install -e ".[ui]"` points Python at the current AgentDebugX `src/`;
- `dsh plugin ... add <directory>` links the DSH profile to this plugin folder.

After pulling or editing AgentDebugX, restart `dsh web`; Python imports the
updated AgentDebugX source when the bridge process starts. Re-run the editable
install only when `pyproject.toml`, dependencies, or package metadata changed.
After editing the JavaScript bridge/plugin, restart DSH as well. No repack or
reinstall is needed for this linked development setup.

Published npm/tarball installations are copies instead of links. For those,
bump the plugin version, run `pnpm pack` or publish to npm, update the DSH
profile dependency, and restart DSH.

## Use

AgentDebugX is loaded as a Cordis plugin, not a Harness skill. After at least
one Harness turn has completed:

```text
/agentdebug status
/agentdebug capabilities
/agentdebug diagnose
/agentdebug open
```

The model-facing tools are:

- `agentdebug_diagnose`: diagnose this DSH session through its latest completed
  turn boundary;
- `agentdebug_analyze_trace`: normalize and diagnose an existing trajectory
  file or OSWorld trajectory directory inside a configured trace root;
- `agentdebug_capabilities`: return the installed integration contract,
  formats, diagnosis mode, and current limitations.

Both diagnosis tools take a `mode`:

- `heuristic` (default) runs AgentDebugX's deterministic
  Detect-Attribute-Recover pipeline and makes no model calls;
- `deep` runs the DeepDebug profile, seeded with the heuristic findings.

Deep mode needs no extra API key: AgentDebugX's `LLMClient` protocol is
satisfied by an adapter that calls back into the Harness host over the same
pipe, so diagnosis runs on the model the session already uses. Set
`llmProvider` and `llmModel` together to pin a different model, which is also
how you get a second opinion from a model that did not produce the trace.

If a deep run fails, the bridge returns the deterministic report with a
`deepError` explaining why, rather than discarding the result. Automatic
per-turn capture never calls a model.

LLM judge, OSWorld GUI root-cause analysis, standalone LLM attribution, rerun,
batch processing, and Error Hub sharing stay on the `agentdebug` CLI against
the same store;
`agentdebug_capabilities` reports them so the model recommends the real command
instead of assuming the product lacks the feature. That tool reads the
installed package's own registries (version, ingest formats, and every
detect/attribute/recover component with its default and LLM requirement), so
the answer cannot drift from the AgentDebugX build in use.

## Opening the visualization automatically

After every completed turn the plugin captures the trace and opens the
AgentDebugX viewer on that turn's step:

```text
http://127.0.0.1:7777/trace/<trace_id>/event/<event_id>
```

`autoOpen` accepts `turn` (default, open after every completed turn),
`session` (open once per session), and `off`. The plugin checks `/healthz`
first, so a dashboard that is not running produces a log warning instead of a
browser tab, and it never reopens a page it already opened. Explicit
`agentdebug_diagnose` and `/agentdebug diagnose` runs jump straight to the
report's root-cause event when the report identifies one. Auto-opening after a
turn requires `autoCapture`, and the dashboard must serve the same `store`.

Heuristic detection reasons over events, so a benchmark trace scored as a
failure can still return zero findings. When the source trace carries an
outcome, the tool result repeats it under `recordedOutcome`, so "no findings"
is never mistaken for "the task succeeded". Use the CLI (`agentdebug diagnose
--mode gui-rca|judge|deep`) for the model-backed root-cause modes.

Start the full AgentDebugX dashboard separately:

```powershell
agentdebug serve --store-sqlite .agentdebug\agentdebug.sqlite
```

Then open `http://127.0.0.1:7777`.

## Configuration

The default bundle row is:

```yaml
- insert:
    - id: agentdebugx
      name: dsh-agentdebugx
      config:
        python: python
        store: .agentdebug/agentdebug.sqlite
        dashboardUrl: http://127.0.0.1:7777
        traceRoots:
          - .
        timeoutMs: 120000
        autoCapture: true
        autoOpen: turn
```

Environment shortcuts:

- `AGENTDEBUGX_PYTHON`
- `AGENTDEBUGX_STORE`
- `AGENTDEBUGX_DASHBOARD_URL`
- `AGENTDEBUGX_TRACE_ROOTS` (semicolon-separated on Windows, colon-separated
  elsewhere)
- `AGENTDEBUGX_AUTO_OPEN` (`turn`, `session`, or `off`)

`deepTimeoutMs` (default 900000) bounds a deep run, which issues several model
calls and therefore takes much longer than `timeoutMs` allows for the
heuristic path.

Harness patch layers replace a row's complete `config`; when overriding this
row, repeat every setting you need.

## Data and security

The plugin runs in the trusted Harness host process and launches a local Python
process. Session snapshots may contain prompts, model responses, tool
arguments, command output, paths, and system prompt material. Storage remains
local by default. The dashboard binds to `127.0.0.1`; do not expose it remotely
without a separate authentication and TLS boundary.

`agentdebug_analyze_trace` can read only paths under `traceRoots`. Keep this
allowlist narrow; add an OSWorld results directory explicitly when the model
needs to analyze traces outside the DSH working directory.

`$DSH_HOME/sessions` is appended to the readable roots automatically so the
model can debug Harness's own past sessions. Point `dshSessionsRoot` at a
different directory to override it, or set it to an empty string to keep
Harness's session history out of reach.

## Debugging saved traces

`agentdebug_analyze_trace` accepts two sources beyond the live session:

- a past Harness session, stored as
  `$DSH_HOME/sessions/<workspace>/session-<uuid>/session.jsonl.zstd`
  (on Windows `$DSH_HOME` defaults to a `dsh-*` folder under `%TEMP%`).
  Pass either the session directory or the log file;
- trace and trajectory files in the open workspace, including OSWorld
  trajectory directories.

Persisted session logs are a concatenated-Zstandard container that Node decodes
frame by frame, and they are mapped through the same code path as the live
session feed, so turn, step, and tool-call linkage is preserved rather than
flattened by generic format detection.

`assistant/chunk` deltas are not duplicated into AgentDebugX. The assembled
assistant message is retained, while the number of skipped chunks is recorded
as trajectory metadata.

## Distribution and discovery

DeepSeek Harness currently does not accept external pull requests. Community
plugins are distributed independently:

1. publish this package to npm, ship a tarball, or install from GitHub;
2. add the GitHub repository topic `dsh-plugin`;
3. community marketplaces index that topic automatically; no marketplace PR is
   required.

Publishing prebuilt/plain JavaScript avoids pnpm's Git `prepare`/`allowBuilds`
permission flow. This package intentionally has no install script.

Official references:

- [Plugin packaging](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.md)
- [Contributing policy](https://github.com/deepseek-ai/deepseek-harness/blob/master/CONTRIBUTING.md)
- [DSH Plugin Marketplace discussion](https://github.com/deepseek-ai/deepseek-harness/discussions/442)

## Development

```powershell
pnpm install
$env:PYTHONPATH = "C:\path\to\AgentDebugX\src"
pnpm test
pnpm test:bridge
pnpm pack
```

The tests treat AgentDebugX as a read-only dependency. Compatibility changes
belong in this adapter unless a generally reusable AgentDebugX public API is
independently justified.
