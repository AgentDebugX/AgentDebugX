---
hide:
  - toc
---

<div class="hero" markdown>
<div class="docs-eyebrow">DOCUMENTATION · v1</div>

# AgentDebugX Documentation

Install AgentDebugX, diagnose a failed agent trace, and prepare an evidence-backed rerun in one guided workflow.

<div class="hero-actions" markdown>
[Start the 5-minute tutorial](getting-started/quickstart.md){ .md-button .md-button--primary }
[Installation](getting-started/installation.md){ .md-button }
</div>

<div class="hero-install"><span>Install</span><code>python -m pip install agentdebugx</code></div>
</div>

<div class="workflow-strip" aria-label="AgentDebugX workflow">
<strong>Trace</strong><span>→</span><strong>Ingest</strong><span>→</span><strong>Detect</strong><span>→</span><strong>Attribute</strong><span>→</span><strong>Recover</strong><span>→</span><strong>Rerun</strong>
</div>

AgentDebugX is local-first. Deterministic ingest and diagnosis can run without an external model. LLM-backed diagnosis, GUI root-cause analysis, and live reruns are enabled only when you configure the corresponding model or application-owned runner.

## Start from your situation

<div class="route-grid">
<a href="getting-started/quickstart/">
<strong>I have a JSON or JSONL trace</strong>
<span>Normalize it and run the first local diagnosis.</span>
</a>
<a href="reference/python-api/">
<strong>I am building a Python agent</strong>
<span>Record events with the embedded API.</span>
</a>
<a href="guides/gui-rca/">
<strong>I am debugging OSWorld or a GUI agent</strong>
<span>Inspect actions and screenshots with GUI RCA.</span>
</a>
<a href="guides/rerun/">
<strong>I already have a diagnostic report</strong>
<span>Plan, simulate, or execute a controlled rerun.</span>
</a>
</div>

## The documented workflow

<div class="feature-grid" markdown>
<div markdown>
### 1. Normalize
Convert framework and benchmark exports into the portable `AgentTrajectory` schema.
</div>
<div markdown>
### 2. Diagnose
Detect visible failures, attribute the responsible event, and package recovery guidance.
</div>
<div markdown>
### 3. Validate
Build an auditable plan or use a configured live runner. Simulations remain explicitly labeled.
</div>
</div>

!!! note "Package and import names"

    Install the distribution as `agentdebugx`, then import it in Python as `agentdebug`.
