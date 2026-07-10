# Case Study 1 Commands

Claude Code's exported transcript collapses shell commands as "Ran N shell
commands". These are the expanded commands recovered from the recorded
terminal capture for the cross-framework Hermes diagnosis.

```bash
agentdebug doctor 2>&1 | head -50

mkdir -p .agentdebug && agentdebug ingest \
  examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl \
  --format hermes \
  --out .agentdebug/hermes_multiwavelength.trajectory.json 2>&1

agentdebug diagnose .agentdebug/hermes_multiwavelength.trajectory.json \
  --mode heuristic \
  --attributor none \
  --recovery none \
  --traceback \
  --no-color 2>&1

agentdebug diagnose .agentdebug/hermes_multiwavelength.trajectory.json \
  --mode deep \
  --attributor binary-search \
  --recovery self-refine \
  --out .agentdebug/hermes_multiwavelength.deep.report.json \
  --traceback \
  --no-color 2>&1
```
