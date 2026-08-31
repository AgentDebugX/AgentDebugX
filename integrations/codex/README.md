# Codex plugin

Native Codex marketplace bundle for project-scoped AgentDebugX capture and
explicit trajectory diagnosis. It uses the installed `agentdebug` CLI, found
through `PATH`.

The plugin is the only supported way to install AgentDebugX capture into Codex.
AgentDebugX does not edit Codex hook configuration on your behalf.

## What it contains

- `hooks/hooks.json`: `SessionStart`, `UserPromptSubmit`, `Stop`, and
  `SessionEnd` hooks that all dispatch
  `agentdebug integrations capture dispatch --platform codex`.
- `skills/agentdebug/`: the canonical AgentDebug skill, generated from
  `src/agentdebug/integrations/agentdebug_skill/`.

Codex CLI `>=0.145.0` is required. See
[Codex support](../../src/agentdebug/capture/hosts/CODEX_SUPPORT.md) for the
compatibility contract.

## Install

```bash
codex plugin marketplace add AgentDebugX/AgentDebugX
codex plugin add agentdebug@agentdebug
```

`marketplace add` accepts a local path, `owner/repo[@ref]`, or a Git URL. The
shorthand above resolves once `.agents/plugins/marketplace.json` is on the
repository's default branch; to install from a local checkout, pass its
**absolute** path instead. These commands run from the project being captured,
so a relative path resolves against that project rather than the checkout.

Codex installs plugins globally rather than per project, so the plugin becomes
available to every project you open. That does **not** make capture global.
Consent, configuration, and storage remain scoped to the individual project:
capture only runs where `.agentdebug/capture.json` enables it, and captured
sessions, traces, and diagnoses are written under that project's
`.agentdebug/` directory.

## Capture versus diagnosis

Enable capture per project:

```bash
agentdebug integrations capture enable --platform codex --project . --json
```

After that, capture is automatic, local, and silent: when capture is disabled
or the project has not consented, the hook exits immediately without writing
anything. Diagnosis is never automatic.
It runs only when you explicitly ask AgentDebug for it, and the skill then runs
`agentdebug run --current --profile deep --json` inside that session. Retrying
or re-rolling out the agent's work requires separate authorization.

## Lazy session creation

Nothing durable is written when a session starts. The first real
`UserPromptSubmit` writes the session's capture context, and the first
completed response boundary writes the first trace. `--current` becomes
diagnosable only after that trace exists.

## Platform support

Automatic capture is validated on Linux. macOS is expected to work, since the
hook path is POSIX-identical, but it has not been validated end to end. Windows
is unvalidated.

Codex `--current` does not depend on the shell-specific environment export that
the Claude Code plugin uses: it resolves the session from `CODEX_THREAD_ID`,
which Codex sets itself. The unvalidated Windows areas are CLI command lookup
from the hook, path quoting, and atomic file replacement while another process
holds a target file open.

## More

- [Capture quickstart](../../CAPTURE_QUICKSTART.md) for the end-to-end
  workflow, including disabling capture and removing the plugin.
- The bundled skill is generated. Edit
  `src/agentdebug/integrations/agentdebug_skill/` and run
  `PYTHONPATH=src python scripts/sync_plugin_skills.py`; never edit the copy
  under `plugins/` directly.
