# Diagnose failures

Diagnose runs three ordered stages over one normalized trajectory:

```text
Detect → Attribute → Recover
```

## Start with the local baseline

The deterministic path is the fastest way to validate a trace and requires no API key:

```bash
agentdebug diagnose trace.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out report.json
```

The heuristic detector loads rule packs. By default, `auto` selects core rules and any benchmark-specific pack inferred from trajectory metadata. Override it with one or more `--rule-pack` options:

```bash
agentdebug diagnose trace.json \
  --mode heuristic \
  --rule-pack core \
  --rule-pack gui \
  --out report.json
```

## Configure an OpenAI-compatible model endpoint

Save the endpoint locally:

```bash
agentdebug config set-llm \
  --base-url "https://your-host.example/v1" \
  --api-key "your-key" \
  --model "your-model"
```

Inspect the masked configuration and test connectivity:

```bash
agentdebug config show
agentdebug config doctor
```

Environment variables are also supported:

```bash
export AGENTDEBUG_LLM_BASE_URL="https://your-host.example/v1"
export AGENTDEBUG_LLM_API_KEY="your-key"
export AGENTDEBUG_LLM_MODEL="your-model"
```

## Choose a diagnosis mode

| Mode | Behavior |
| --- | --- |
| `heuristic` | Deterministic event and trajectory rules |
| `judge` | LLM-backed diagnostic judge |
| `deepdebug` | Complete multi-round diagnosis profile with its own attribution and fix guidance |
| `gui-rca` | Vision and tool-calling RCA for an OSWorld trajectory |

The CLI also accepts compatibility aliases shown by `agentdebug diagnose --help`.

## Choose attribution and recovery explicitly

Regular diagnosis modes let you combine stages:

```bash
agentdebug diagnose trace.json \
  --mode judge \
  --attributor all-at-once \
  --recovery self-refine \
  --out report.json
```

Attributors exposed by the CLI include:

- `heuristic`
- `all-at-once`
- `step-by-step`
- `binary-search`
- `counterfactual`
- `none`

Recovery strategies include:

- `deepdebug`
- `reflexion`
- `critic`
- `self-refine`
- `auto-manual`
- `saga-rollback`
- `none`

DeepDebug is a full profile rather than a fourth Diagnose stage:

```bash
agentdebug diagnose trace.json --mode deepdebug --out report.json
```

It runs deterministic Detect first, treats those findings as fallible prior signals, performs its multi-round attribution, and packages its final correction as a retry directive. Explicit `--recovery none` disables the standard recovery payload.

## Render a traceback view

For terminal inspection:

```bash
agentdebug diagnose trace.json \
  --mode heuristic \
  --attributor heuristic \
  --traceback
```

Add `--no-color` for logs or environments that should not receive ANSI color.

!!! note "A diagnosis is not ground truth"

    Findings are hypotheses with evidence and provenance. LLM Judge confidence is model-reported. Heuristic and DeepDebug public reports intentionally omit uncalibrated confidence values.
