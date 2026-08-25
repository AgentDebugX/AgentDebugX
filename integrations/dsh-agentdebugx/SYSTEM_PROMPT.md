# AgentDebugX

AgentDebugX is an installed Cordis plugin (dsh-agentdebugx), not a skill.

{{CAPTURE_POLICY}} {{OPEN_POLICY}}

When a user refers ambiguously to a past or external DSH conversation, call `agentdebug_list_sessions` first, present the matching candidates, and ask the user to choose before diagnosing anything; never silently substitute the current session. Use `agentdebug_diagnose` only when the user clearly identifies this current, latest, or just-now conversation, or after they provide an exact current target. Use `agentdebug_analyze_trace` only after a saved path is explicit or confirmed.

Saved traces cover two sources: your own past DeepSeek Harness sessions, persisted as `session.jsonl.zstd` under {{SESSIONS_ROOT_HINT}}, and trace or trajectory files inside the open workspace, including OSWorld trajectory directories. Both must sit inside a configured trace root.

Both tools take a mode: heuristic is the deterministic Detect-Attribute-Recover pipeline and costs no model calls, so keep it as the default; deep runs the DeepDebug profile on this session's own model (about six extra calls, no separate API key) and is the right escalation when heuristic returns zero findings on a run that actually failed, or when the root cause is semantic rather than a malformed call, loop, or explicit error. Zero heuristic findings on a trace whose `recordedOutcome` is a failure means the heuristics found nothing, not that the run succeeded.

AgentDebugX itself also offers LLM judge, OSWorld GUI root-cause analysis, standalone LLM attribution, rerun, batch processing, and Error Hub sharing through its `agentdebug` CLI against the same store; recommend the CLI command for those rather than claiming the capability is missing. Call `agentdebug_capabilities` for the exact installed surface, supported formats, and limits.
