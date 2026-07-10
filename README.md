<div align="center">

# AgentDebugX

**Failure attribution and recovery for LLM agents — from a trace to a root cause to a fix.**

[![PyPI](https://img.shields.io/badge/pip-agentdebugx-3775A9)](https://pypi.org/project/agentdebugx/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)

[Website](https://www.agentdebugx.com) · [GitHub](https://github.com/AgentDebugX/AgentDebugX) · [Demo video](https://youtu.be/ztni6w0o_l8)

</div>

---

When an LLM-agent run fails, the visible crash is usually a downstream symptom of an
earlier decision — a dropped constraint, a stale memory read, a lost handoff. Tracing
tools show *what* happened; they leave you to work out *which* step was responsible,
*why*, and *what to change*.

**AgentDebugX** is a self-hostable debugging layer that closes that gap. Point it at a
live run or an exported log and it returns a typed diagnosis: the responsible step, a
taxonomy-grounded explanation with evidence, and a concrete fix you can rerun — all in a
local web console, a CLI, or an agentic skill your own agents can invoke.

Everything runs on your machine. No account, no hosted service, no data leaves your host.

## Highlights

- **DeepDebug** — a multi-turn, tool-using diagnosis agent that reads the whole trace,
  re-investigates along its structure (cascade walk / bisect), cross-examines its own
  hypotheses, and returns an auditable verdict with one concrete fix.
- **Cost-staged attribution** — free deterministic detectors → a single LLM-judge pass →
  a family of localizers (all-at-once, step-by-step, binary-search) that trade cost for
  resolution.
- **End-to-end recovery** — turn a diagnosis into a rerun-ready fix (Reflexion, CRITIC,
  Self-Refine, AutoManual, saga-rollback recoverers), gated behind explicit human/policy
  approval.
- **Portable trace schema** — one framework-agnostic format with runtime adapters
  (LangGraph, CrewAI, OpenAI Agents SDK, OpenTelemetry, raw ReAct) and offline importers.
- **Local console** — a no-build single-page app served straight from the wheel; aligns
  the agent's own trace with AgentDebugX's error trace, row by row.
- **Error Hub** — package scrubbed failure bundles (trace + diagnosis + fix) as CI
  fixtures or a shareable cross-team corpus that doubles as DeepDebug's long-term memory.
- **Computer-use agents** — an OSWorld importer and a GUI root-cause mode reason over the
  screenshot-and-action channel, not just text.

## Install

```bash
pip install agentdebugx
```

Optional extras (install only what you need):

```bash
pip install "agentdebugx[ui]"        # local web console (FastAPI + Uvicorn)
pip install "agentdebugx[langgraph]" # LangGraph adapter
pip install "agentdebugx[gui]"       # computer-use / OSWorld GUI RCA
pip install "agentdebugx[all]"       # every optional integration
```

The package is imported as `agentdebug`:

```python
import agentdebug
```

## Quick start (library)

Instrument a run with one context manager and analyze it in place:

```python
from agentdebug import AgentDebug, EventType

dbg = AgentDebug()
with dbg.trace(goal="Book a refundable NYC→SFO flight", framework="my-agent") as t:
    t.record(EventType.PLAN, agent_name="planner",
             output="search cheapest fares")          # drops the 'refundable' constraint
    t.record(EventType.TOOL_RESULT, agent_name="browser", step_index=3,
             error="Checkout failed: refund_policy required")
    report = t.analyze()

print(report.summary)
for finding in report.findings:
    print(finding.failure_mode_id, finding.step_index, finding.confidence)
```

The report names the **responsible** step (the planner at step 0), not just the visible
crash (the browser at step 3).

## Quick start (local console)

```bash
pip install "agentdebugx[ui]"
agentdebug serve            # opens the console at http://127.0.0.1:7777
```

The console opens directly into the workspace — inspect traces, re-analyze any step with
the LLM judge or DeepDebug, request a debug continuation, and rerun from a chosen step.
It reads the same local store the library writes; nothing is uploaded.

## CLI

The `agentdebug` CLI exposes the full pipeline:

| Stage | Command | Purpose |
|-------|---------|---------|
| Normalize | `agentdebug ingest <export>` | Convert an external trace export into `AgentTrajectory` JSON |
| Diagnose | `agentdebug diagnose --store-jsonl <path>` | Detectors + attribution + recovery planning |
| Inspect | `agentdebug serve` / `inspect` | Local web console over a store |
| Deep dive | `agentdebug act deep <trace-id>` | Run the multi-turn DeepDebug agent |
| Recover | `agentdebug rerun --report <report.json>` | Rerun an agent from a diagnostic report |
| Share | `agentdebug hub push / pull` | Package or fetch scrubbed Error Hub bundles |
| Skills | `agentdebug integrations` | Emit host-runtime integrations (e.g. Claude Code skill) |
| Health | `agentdebug doctor` | Report adapter and integration availability |

LLM-backed commands read credentials from the environment:

```bash
export AGENTDEBUG_LLM_BASE_URL=...   # any OpenAI-compatible endpoint
export AGENTDEBUG_LLM_API_KEY=...
export AGENTDEBUG_LLM_MODEL=...       # optional
```

Run `agentdebug <command> --help` for the authoritative, version-specific flags.

## How it works

```
 capture / import ──▶ AgentTrajectory ──▶ Detect ──▶ Attribute ──▶ Recover ──▶ Rerun
   adapters, logs        portable schema   rules,      responsible   ranked      from before/
   (any framework)                         judge       step/agent    fixes       at/after cause
                                             │
                                             └── hard cases ──▶ DeepDebug (multi-turn agent)
```

A diagnosis is **layered on top of** the recorded trace, never written back into it, so
one run can be re-analyzed by any method, compared across runs, and shared without losing
its ground truth. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the DeepDebug
design and [`docs/TRACE_SCHEMA.md`](docs/TRACE_SCHEMA.md) for the event schema.

## Examples

The [`examples/`](examples/) directory contains runnable end-to-end scripts:

- `basic_usage.py` — record, analyze, inspect.
- `multi_agent_cascade.py` — upstream root-cause attribution across a handoff cascade.
- `langgraph/`, `crewai/`, `autogen_roundrobin_deepdebug.py` — framework adapters.
- `taxonomy_induction_demo.py` — propose new failure modes from a corpus.
- `claude_skill_integration/` — invoke AgentDebugX as an agentic skill.

## Repository layout

```
src/agentdebug/     core package: schema, detectors, judge, attribution, DeepDebug,
                    recovery, Error Hub, CLI, and the local web console (inspect/ui/)
cua_debugger/       computer-use / OSWorld GUI root-cause tooling
examples/           runnable usage examples
docs/               architecture and trace-schema reference
```

## Safety

AgentDebugX is **local-first**: traces stay on your machine and sharing is opt-in. A
diagnosed fix can write to the world, so recovery is **suggest-only** — application stays
behind an explicit human or policy gate. Diagnostic labels and fixes carry confidence and
evidence; treat them as ranked hypotheses, not ground truth. Configure redaction,
retention, and access control before collecting production traces.

## License

MIT — see [LICENSE](LICENSE).
