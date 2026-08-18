# Local Trajectory Data

This directory is the project-local workspace for raw, extracted, and
normalized agent trajectories used during development.

Its data contents are intentionally ignored by Git; this README remains
tracked so fresh checkouts retain the directory and its purpose.

Current local layout:

```text
trajectory_data/
  alfworld_3_traces.jsonl
  alfworld_normalized/
  osworld/
```

The Dashboard trace database remains under `.agentdebug/traces.sqlite`.
After moving or renaming trajectory directories, re-import filesystem-backed
traces so artifact URIs and `metadata.source_dir` point to their new location.
