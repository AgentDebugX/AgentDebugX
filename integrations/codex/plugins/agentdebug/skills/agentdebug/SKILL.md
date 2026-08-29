---
name: agentdebug
description: Use AgentDebugX only when the user explicitly asks to use AgentDebug, AgentDebugX, or the agentdebug skill. Do not trigger for generic debugging requests.
---

# AgentDebugX

Capture is project-scoped, local, and automatic after explicit activation.
Diagnosis is never automatic.

## Project setup

Use the pinned private runtime; do not install into the project environment:

```bash
uvx --from agentdebugx==0.4.0 agentdebug integrations capture enable --native-plugin --platform codex --project "$PWD" --json
```

Inspect or disable capture with the same command, replacing `enable` with
`status` or `disable`. Disabling preserves existing `.agentdebug` artifacts.

Capture stores normalized trajectories in
`<project>/.agentdebug/agentdebug.sqlite`. Hooks do not run diagnosis or modify
project code.

## Diagnosis

For an explicit request to inspect this Codex session, run:

```bash
uvx --from agentdebugx==0.4.0 agentdebug run --current --profile quick --json
```

For a supplied trajectory path or trace ID, pass it explicitly and use the
appropriate store option. Never substitute the newest trajectory for the
current session.

Interpret the candidate root cause and supporting events for the user's goal.
Do not treat diagnosis as permission to patch or retry unless the user asks.
