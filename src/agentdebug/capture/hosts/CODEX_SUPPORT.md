# Codex Capture Support

AgentDebugX supports Codex CLI `>=0.145.0`; `0.149.1` is the currently tested
version. Capture does not reject newer versions solely because their version is
unknown.

The compatibility contract uses four stable hooks: `SessionStart`,
`UserPromptSubmit`, `Stop`, and `SessionEnd`. The first three are present by
Codex `0.120.0`; `SessionEnd` is present from `0.145.0`. Later audited releases
through `0.150.0` retain them.

Hook payloads must provide `session_id`, `transcript_path`, `cwd`, and
`hook_event_name`. Rollouts are read from the host-supplied path and normalized
from `session_meta`, `response_item`, `event_msg`, and compatible older message
records. Unknown fields and record types are ignored. Reasoning, token
accounting, developer messages, and managed-skill reads are excluded.

The contract was checked against OpenAI Codex tags `rust-v0.120.0`,
`rust-v0.130.0`, `rust-v0.140.0`, `rust-v0.145.0`, `rust-v0.149.1`, and
`rust-v0.150.0`. Synthetic fixture coverage contains no copied user transcript.
