# Batch Workflows

Batch workflows apply the existing ingest and diagnose contracts to independent
JSON records without allowing one malformed sample to stop the collection.

## Input Semantics

- A directory is scanned recursively for `*.json`; each file is one record.
- A `.jsonl` file is read line by line; each non-empty line is one record.
- A single `.json` file is accepted as a one-record batch.

JSONL records are intentionally independent. Use regular `agentdebug ingest`
for formats where one JSONL stream represents one trajectory.

## Outputs

`batch ingest` writes one `*.trajectory.json` per successful record.

`batch diagnose` writes normalized inputs under `trajectories/` and reports
under `reports/`, allowing any result to be inspected or rerun independently.

Both commands write `batch-summary.json` with counts, paths, and per-record
errors. A partial batch returns a non-zero CLI status after preserving all
successful outputs.
