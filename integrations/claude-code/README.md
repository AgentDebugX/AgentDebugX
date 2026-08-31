# Claude Code plugin

Native Claude Code marketplace bundle for project-scoped AgentDebugX capture
and explicit trajectory diagnosis. It uses the installed `agentdebug` CLI,
found through `PATH`.

The plugin is the only supported way to install AgentDebugX capture into Claude
Code. AgentDebugX does not edit `.claude/settings.json` on your behalf.

## What it contains

- `hooks/hooks.json`: `SessionStart`, `UserPromptSubmit`, `Stop`,
  `TaskCompleted`, and `SessionEnd` hooks that all dispatch
  `agentdebug integrations capture dispatch --platform claude`.
- `skills/agentdebug/`: the canonical AgentDebug skill, generated from
  `src/agentdebug/integrations/agentdebug_skill/`. Claude Code exposes it as
  `/agentdebug:agentdebug`.

## Install

Run from the project whose sessions you want to capture, then start a new
session:

```bash
claude plugin marketplace add AgentDebugX/AgentDebugX --scope project
claude plugin install agentdebug@agentdebug --scope project
```

Project scope keeps the plugin with the repository rather than the user
account.

`marketplace add` accepts a URL, a path, or a GitHub repo. The shorthand above
resolves once `.claude-plugin/marketplace.json` is on the repository's default
branch; to install from a local checkout, pass its **absolute** path instead.
These commands run from the project being captured, so a relative path resolves
against that project rather than the checkout.

## Capture versus diagnosis

Installing the plugin does not enable capture. Consent is separate and
per-project:

```bash
agentdebug integrations capture enable --platform claude --project . --json
```

After that, capture is automatic, local, and silent: when capture is disabled
or the project has not consented, the hook exits immediately without writing
anything. Diagnosis is never automatic.
It runs only when you explicitly ask AgentDebug for it, and the skill then runs
`agentdebug run --current --profile deep --json` inside that session. Retrying
or re-rolling out the agent's work requires separate authorization.

## Lazy session creation

`SessionStart` only exports the future capture-context path into the session
environment. Nothing durable is written until the first real
`UserPromptSubmit`, and `--current` becomes diagnosable only after that session
produces a completed captured trace. Starting a session and exiting without
prompting leaves no context, session, or trace behind.

## Platform support

Automatic capture is validated on Linux. macOS is expected to work, since the
hook path is POSIX-identical and project paths containing spaces are quoted
correctly, but it has not been validated end to end. Windows is unvalidated.

One Windows limitation is known rather than merely untested. `agentdebug run
--current` resolves the session through an environment variable that the
`SessionStart` hook exports into `CLAUDE_ENV_FILE` using POSIX shell syntax.
Where that file is not sourced by a POSIX shell, the variable never reaches the
session and `--current` cannot resolve it. Capture itself is unaffected and
traces are still written, so diagnose by listing traces and passing an explicit
trace ID:

```bash
agentdebug list --store-sqlite .agentdebug/agentdebug.sqlite
agentdebug run <trace-id> --store-sqlite .agentdebug/agentdebug.sqlite \
  --profile deep --json
```

## More

- [Capture quickstart](../../CAPTURE_QUICKSTART.md) for the end-to-end
  workflow, including disabling capture and uninstalling.
- The bundled skill is generated. Edit
  `src/agentdebug/integrations/agentdebug_skill/` and run
  `PYTHONPATH=src python scripts/sync_plugin_skills.py`; never edit the copy
  under `plugins/` directly.
