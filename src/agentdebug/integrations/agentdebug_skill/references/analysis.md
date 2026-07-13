# Analysis Heuristics

Use these heuristics after `agentdebug diagnose` produces a report. They help
explain the result; they do not replace the CLI diagnosis.

## Failure Location

- A tool error often marks where failure surfaced, not necessarily where it
  began.
- A final wrong answer can originate several steps earlier in planning,
  retrieval, tool selection, or environment assumptions.
- Repeated identical failed calls usually imply recovery/planning failure, not
  an unreliable tool.

## Common Root-Cause Patterns

- Missing file, command, or dependency: inspect environment/setup assumptions
  before changing application code.
- Permission denial: distinguish tool permission policy from application
  failure.
- Hallucinated path, URL, command, or API name: treat as an
  environment-assumption failure.
- Tool succeeds but answer is wrong: inspect observation interpretation and
  whether relevant evidence was ignored.
- Final answer failure without a tool exception: look for earlier evidence
  selection, retrieval, calculation, constraint-following, or stop-condition
  failures. Deterministic heuristics may only flag loops or suspicious tool
  patterns; that is still useful evidence, not a complete correctness judge.
- Long search traces with many successful tool calls often need semantic
  diagnosis. Escalate to `--mode judge` or `--mode deep` only after the
  deterministic pass and only if LLM credentials are available.
- A `tool.result` marked as success can still contain a nested application
  error payload. Inspect the event output/error text before assuming the tool
  was semantically successful.

## Reading Reports

Traceback output is optimized for quick explanation:

- root-cause candidate first,
- cascaded findings after it,
- `AgentFailure[...]` summary at the end.

JSON output is better when recovery or attribution is enabled:

- `findings`: diagnosis findings,
- `attribution`: optional blame localization payload,
- `recovery`: optional recovery metadata,
- `suggestions`: recovery text proposals.

When reporting to the user, include:

1. trace id and framework,
2. candidate root-cause step/event id,
3. failure mode,
4. the evidence quoted or summarized from the report,
5. whether recovery guidance was generated,
6. any limitation, such as "final-answer correctness was not independently
   judged."

## Correlating With Code

Only inspect local code after the diagnosis identifies a concrete artifact:
tool implementation, script or command, prompt/template, config, dependency,
or environment setup. Do not browse the whole codebase before running
AgentDebugX.
