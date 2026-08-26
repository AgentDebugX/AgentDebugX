# Automatic Capture

This package converts supported Claude Code and Codex transcript snapshots into
cumulative AgentDebugX trajectories. Capture is opt-in, silent, and fail-open;
it never starts diagnosis or invokes an LLM.

## Flow

1. Validate the host notification and capture configuration.
2. Normalize, filter, and redact complete transcript records.
3. Atomically store the trajectory and capture receipt in project SQLite.
4. Reconcile pending or failed receipts on a later capture event.

Host-specific parsing belongs in `hosts/`; shared identity, filtering,
snapshot, repository, and orchestration logic stays in this package.
