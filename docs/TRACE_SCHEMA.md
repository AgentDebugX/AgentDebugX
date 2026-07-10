# 04 — Canonical Trace Schema

## 1. Goal

A single, framework-agnostic representation of an agent run that:

1. **Wire-compatible with OpenTelemetry GenAI semantic conventions** — every backend that speaks OTel can ingest AgentDebugX traces verbatim.
2. **Rich enough for failure attribution** — captures intent, observations, intermediate state, parent–child relationships across agents.
3. **Replayable** — enough info to re-execute a step under a counterfactual or with a different model.
4. **Versioned** — schema lives in `agentdebugx.schema` with a `schema_version` field on every record.

## 2. Top-level objects

```
Run                # one user-initiated agent invocation
├── Session        # logical container (multi-run threads, e.g. chat sessions)
└── Trace          # tree of Spans
    └── Span       # one operation: agent, llm, tool, retrieval, handoff
        ├── Events # discrete records inside a span (messages, decisions)
        └── Links  # cross-span references (e.g. handoff target)
```

Mapping to OTel:

- `Run` ↔ root `Span` with `gen_ai.operation.name=invoke_workflow`
- `Trace` ↔ OTel trace
- `Span` ↔ OTel span
- `Events` ↔ OTel span events (`gen_ai.user.message`, `gen_ai.assistant.message`, `gen_ai.tool.message`, plus AgentDebugX custom events)

## 3. Pydantic models (schema-of-record)

```python
class Run(BaseModel):
    schema_version: str = "0.1.0"
    run_id: str                    # ULID
    session_id: str | None
    project: str
    framework: FrameworkId         # autogen | langgraph | crewai | …
    status: Literal["pending", "running", "succeeded", "failed", "aborted"]
    started_at: datetime
    finished_at: datetime | None
    config: dict                   # model, temperature, tools, etc.
    inputs: dict
    outputs: dict | None
    error: ErrorInfo | None
    trace_id: str                  # OTel trace_id

class Span(BaseModel):
    span_id: str
    parent_span_id: str | None
    trace_id: str
    operation: Literal[
        "invoke_workflow", "create_agent", "invoke_agent",
        "chat", "text_completion", "embeddings",
        "execute_tool", "retrieve_context",
        "handoff", "delegate",
    ]
    agent: AgentRef | None
    started_at: datetime
    finished_at: datetime | None
    status: SpanStatus
    attributes: dict               # all OTel gen_ai.* attributes
    events: list[SpanEvent]
    links: list[SpanLink]
    raw: dict                      # framework-specific original payload (preserved)

class AgentRef(BaseModel):
    id: str                        # gen_ai.agent.id
    name: str                      # gen_ai.agent.name
    description: str | None        # gen_ai.agent.description
    version: str | None
    role: str | None
    tools: list[ToolDef]

class ToolDef(BaseModel):
    name: str
    description: str
    schema: dict                   # JSON schema of args
    side_effect: Literal["none", "read", "write", "external"]
    compensation: str | None       # name of a registered compensation tool

class SpanEvent(BaseModel):
    timestamp: datetime
    name: str                      # gen_ai.user.message etc.
    attributes: dict

class Message(BaseModel):          # used inside chat span events
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart]
    name: str | None
    tool_calls: list[ToolCall] | None
    tool_call_id: str | None

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict
    result: dict | None
    error: str | None
    duration_ms: int | None
```

### 3.1 Runtime-control extension records

The schema should also reserve first-class records for live control decisions,
inspired by Claude Code's hooks, permissions, and subagent lifecycle model.

```python
class DecisionRecord(BaseModel):
    decision_id: str
    span_id: str
    hook_point: Literal[
        "before_tool_use", "after_tool_use",
        "before_agent_step", "after_agent_step",
        "before_handoff", "after_handoff",
        "before_stop", "after_stop",
        "before_recovery_apply", "after_recovery_apply",
        "before_memory_write", "before_external_side_effect",
    ]
    decision: Literal["observe", "annotate", "allow", "block", "escalate", "rewrite", "retry"]
    source: str                    # detector / policy / human / plugin id
    rationale: str
    policy_rule_id: str | None
    human_approval_id: str | None
    payload_before_hash: str | None
    payload_after_hash: str | None

class HandoffContract(BaseModel):
    source_agent_id: str
    target_agent_id: str
    delegation_prompt_hash: str
    expected_output_schema: dict | None
    tool_surface: list[str]
    context_scope: Literal["none", "summary", "selected_events", "full_trace"]
    omitted_context_summary: str | None
    return_payload_hash: str | None
    stop_reason: str | None

class ContextBudget(BaseModel):
    component_id: str
    load_time: Literal["startup", "per_run", "per_step", "on_demand", "offline"]
    context_cost: Literal["none", "metadata", "bounded_excerpt", "full_trace"]
    latency_budget_ms: int | None
    privacy_surface: Literal["local", "model_visible", "external_service"]
```

These records let AgentDebugX answer questions that ordinary traces cannot:

- Was a dangerous action blocked before execution?
- Which policy or hook escalated the action?
- Did a recovery rewrite a payload?
- What exact context did a subagent receive?
- Did a stop gate prevent premature success?

## 4. Required OTel GenAI attributes

Every span MUST carry the appropriate subset:

```
gen_ai.system                    openai | anthropic | google | …
gen_ai.operation.name            chat | execute_tool | invoke_agent | …
gen_ai.agent.id / name           if span is agent-scoped
gen_ai.request.model             on chat / completion spans
gen_ai.response.model
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
gen_ai.request.temperature       optional
gen_ai.request.max_tokens        optional
gen_ai.tool.name                 on execute_tool spans
gen_ai.tool.call.id              on execute_tool spans
gen_ai.tool.definitions          on agent spans (JSON schema list)
```

AgentDebugX adds a small `agentdebugx.*` namespace for non-standardized fields:

```
agentdebugx.step_index           monotonic step counter inside a run
agentdebugx.adapter              which adapter produced this span
agentdebugx.detected_errors      list of DetectedError IDs attached
agentdebugx.blame                Blame object IDs attached
agentdebugx.checkpoint_id        if a checkpoint was taken here
agentdebugx.parent_run_id        if span is part of a retry chain
```

## 5. Trace as a "step list"

Many algorithms (Binary-Search attribution, ddmin, SBFL) need a *linear* view. AgentDebugX derives a canonical step list from the span tree:

```python
def steps(trace: Trace) -> list[Step]:
    """
    Walk the span tree depth-first; emit a Step for each
    invoke_agent | execute_tool | chat | handoff span.
    """
```

A `Step` is a frozen view of a single decision point with all surrounding context (input messages, output messages, tool call, tool result), suitable for replay.

## 6. Storage

| Tier | Format | Purpose |
|---|---|---|
| Hot | SQLite (`runs.db`) | Metadata, indexes, fast lookups |
| Warm | DuckDB | Analytical queries over span attributes |
| Cold | Parquet (one file per run) | Long-term archive, sharing, community sync |

Spans are stored as JSONL inside the Parquet file per run; metadata (run_id, project, framework, status, errors, blame) goes into SQLite for indexing.

## 7. Serialization

- **On-disk:** JSONL (one Span per line) inside Parquet; metadata in SQLite.
- **Wire (export):** OTLP (gRPC + HTTP), matching the OTel collector protocol.
- **Inter-process (event bus):** msgpack for low-latency in-process pub/sub.

## 8. PII scrubbing

Before any cross-machine sync (community corpus, OTel export to third-party), traces pass through a `Scrubber`:

- Configurable regex + presidio-style detectors for emails, phone, SSNs, API keys.
- Per-tool-arg redaction rules registered via `ToolDef.redact_args`.
- Allow-listed retention for benchmarking inputs (GAIA tasks, etc.).

Scrubbing is **on by default for sync, off for local storage**. The local user owns the unscrubbed copy.

## 9. Schema evolution

- `schema_version` semver on every `Run` and `Span`.
- Migrations live in `agentdebugx.schema.migrations.v0_1_0_to_v0_2_0`, automatically applied on read.
- Breaking changes (major bump) require ≥ 1 minor of deprecation notice and a writer-shim.

## 10. Validation

- Pydantic v2 models in `agentdebugx.schema`.
- `agentdebugx.schema.validate(span_dict)` is the canonical entry point.
- A `pytest` suite ensures every adapter emits spans that round-trip through validation.
