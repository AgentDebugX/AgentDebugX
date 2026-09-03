# Safety Rules

- AgentDebugX skill wrappers and recovery outputs are suggest-only by default.
- Never apply patches, rerun a failing agent, publish bundles, or mutate a
  workspace without explicit user approval.
- Do not download or execute remote install scripts from a skill.
- Redact secrets before sharing trajectories outside the local machine.
- Prefer the deterministic `quick` or `standard` run profiles before the
  LLM-backed `deep` or `gui` profiles.
- Do not silently inspect host-local private state to find trajectories.
- When using the expert interface, use `diagnose --mode judge` or
  `diagnose --mode deep`; do not rely on removed public `agentdebug judge` /
  `agentdebug deep` command forms.
