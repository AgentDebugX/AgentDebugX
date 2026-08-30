# GUI / OSWorld root-cause analysis

The GUI RCA path analyzes computer-use trajectories containing actions, screenshots, reward, completion state, and execution errors.

## What the analyzer does

For a failed trajectory, the RCA agent starts at the terminal failure step `F` and walks backwards:

```text
terminal failure F
      ↓ inspect
step F, F-1, F-2, ...
      ↓
earliest step that introduced a new causal mistake
      ↓
root_error_step N
```

For each inspected step, the RCA tools expose textual action details plus input and result screenshots. The final result contains:

- one `root_error_step`,
- a GUI taxonomy tag,
- grounded evidence,
- a proposed correction,
- model-reported confidence,
- concise summaries for inspected steps, and
- the retained thinking trace.

Infeasible OSWorld tasks use a separate prompt branch that checks whether the agent recognized that the task could not be completed.

## Install the needed layer

The GUI RCA package ships with the core install. Add Pillow when screenshots need decoding:

```bash
python -m pip install "agentdebugx[gui]"
```

The heavier lesson-memory and batch application are separate:

```bash
python -m pip install "agentdebugx[gui,gui-memory,gui-app]"
```

## Normalize the OSWorld directory

```bash
agentdebug ingest path/to/osworld-task \
  --format osworld \
  --out osworld-trace.json
```

Keep the source directory and screenshots in place. The normalized trajectory stores a resolved `metadata.source_dir` and screenshot artifact URI references so the RCA tools can reopen the evidence.

## Run standard GUI RCA

Configure an OpenAI-compatible backend that supports both tool calling and vision, then run:

```bash
agentdebug diagnose osworld-trace.json \
  --mode gui-rca \
  --model "your-model" \
  --out gui-report.json
```

The standard analyzer maps the GUI-specific `RCAResult` into the common `DiagnosticReport` format. The primary finding preserves the taxonomy tag, evidence, correction, step index, event ID when found, and inspected-step summaries.

## GUI batch and annotation application

The older batch application is available through:

```bash
python -m agentdebug.gui --help
```

It requires the GUI memory and application extras. Its configuration, result layout, annotation UI, and accuracy commands are documented in the [GUI command reference](../GUI_RCA_COMMANDS.md).

!!! info "Two GUI entry surfaces currently coexist"

    The standard `agentdebug ingest` plus `agentdebug diagnose --mode gui-rca` path integrates with the common AgentDebugX schema and report. `python -m agentdebug.gui` is the package's older batch, memory, and annotation workflow and has a larger dependency set.
