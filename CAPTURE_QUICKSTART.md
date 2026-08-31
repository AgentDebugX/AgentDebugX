# Automatic Capture Quickstart

The AgentDebugX plugins for Claude Code and Codex let an agent debug its own
session. Hooks capture the session automatically after the project opts in, and
the bundled AgentDebug skill diagnoses that captured trajectory when you
explicitly ask for it.

Two boundaries matter throughout this guide:

- **Capture is automatic** once you enable it for a project. It is local,
  project-scoped, silent, and fail-open.
- **Diagnosis is explicit.** It runs only when you ask AgentDebug for it, and
  re-running or repairing the agent's work always requires separate
  authorization.

## 1. Install the CLI

AgentDebugX requires Python 3.9 or later:

```bash
python -m pip install agentdebugx
agentdebug --help
```

The package provides the `agentdebug` command; the plugins invoke it through
`PATH`. If you install into a virtual environment, launch Claude Code or Codex
from that activated environment so the hooks can find it.

## 2. Install the native plugin

Run these from the project whose sessions you want to capture, then start a new
host session.

The marketplace source below is the `AgentDebugX/AgentDebugX` GitHub
repository, which works once the marketplace manifests are published on its
default branch. To install from a local AgentDebugX checkout instead, pass that
checkout's **absolute** path as the source — these commands run from the
project being captured, so a relative path would resolve against the wrong
directory:

```bash
claude plugin marketplace add /abs/path/to/AgentDebugX --scope project
codex plugin marketplace add /abs/path/to/AgentDebugX
```

### Claude Code

```bash
claude plugin marketplace add AgentDebugX/AgentDebugX --scope project
claude plugin install agentdebug@agentdebug --scope project
```

### Codex

```bash
codex plugin marketplace add AgentDebugX/AgentDebugX
codex plugin add agentdebug@agentdebug
```

Codex installs the plugin outside project scope, but capture consent and
storage stay project-specific.

The plugin bundles the canonical AgentDebug skill, so no separate skill
installation is needed. Claude Code exposes it as `/agentdebug:agentdebug`.

## 3. Enable project capture

Installing the plugin never enables capture. Opt in per project and per host:

```bash
agentdebug integrations capture enable --platform claude --project . --json
agentdebug integrations capture enable --platform codex --project . --json
```

Check the current state at any time:

```bash
agentdebug integrations capture status --platform claude --project . --json
```

Prompts, responses, and supported tool events may be captured into
`<project>/.agentdebug/`. Capture never starts a diagnosis, calls an LLM,
modifies your code, or uploads anything.

## 4. Diagnose the current session

Work normally in a new Claude Code or Codex session. The first real prompt
creates the session context; the first completed response creates the first
trace. Launching a host and exiting without prompting creates nothing, and
resuming a captured session keeps its existing identity instead of starting a
new one.

Once at least one response has completed, ask for a diagnosis:

```text
Use AgentDebugX to diagnose this session. Do not modify the project.
```

The skill runs the advocated Deep-mode workflow inside that session:

```bash
agentdebug run --current --profile deep --json
```

`--profile deep` is LLM-backed; see the skill's `references/setup.md` if
credentials are not configured yet. `--profile quick` and `--profile standard`
stay fully local.

`--current` resolves the exact trajectory of the calling host session. It
requires context supplied by that session, so it fails — rather than guessing
the newest project trace — when run from an unrelated terminal or before the
session has a completed trace. To diagnose from another terminal, name the
trace explicitly:

```bash
agentdebug list --store-sqlite .agentdebug/agentdebug.sqlite
agentdebug run <trace-id> --store-sqlite .agentdebug/agentdebug.sqlite \
  --profile deep --json
```

Diagnosis reports what went wrong. Retrying or re-rolling out the agent is a
separate, user-authorized step.

## 5. Find captured traces and runs

Capture state lives under the project:

```text
<project>/.agentdebug/
├── agentdebug.sqlite                  queryable store
└── sessions/<host>/<session-id>/
    ├── session.json                   session index
    ├── traces/0001.json               immutable normalized trajectories
    └── runs/<run-id>.json             diagnoses of this session
```

Each numbered trace is an immutable AgentDebugX trajectory. A run manifest
references its input trace by identity and hash and holds the diagnostic
result; it never writes a second trajectory copy. Diagnoses of trajectories you
supplied explicitly — a file or an imported export — stay in
`.agentdebug/runs/` instead.

See [`src/agentdebug/capture/README.md`](src/agentdebug/capture/README.md) for
the full layout and
[`src/agentdebug/workbench/README.md`](src/agentdebug/workbench/README.md) for
run profiles and manifest contents.

## 6. Stop capture or remove the plugin

Disable capture while keeping every stored artifact:

```bash
agentdebug integrations capture disable --platform claude --project . --json
agentdebug integrations capture disable --platform codex --project . --json
```

The plugin stays installed, so its hook command still starts briefly to check
whether capture is enabled. Disable or remove the plugin to avoid that cost:

```bash
claude plugin disable agentdebug@agentdebug --scope project   # enable to restore
codex plugin remove agentdebug@agentdebug                     # add to restore
```

Uninstall completely:

```bash
claude plugin uninstall agentdebug@agentdebug --scope project
claude plugin marketplace remove agentdebug --scope project

codex plugin remove agentdebug@agentdebug
codex plugin marketplace remove agentdebug
```

Remove the whole `.agentdebug/` directory only when none of its captured
sessions, traces, or diagnostic runs are still needed.
