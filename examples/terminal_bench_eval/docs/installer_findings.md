# Pinned Claude installer validation

Validated 2026-08-15 under SLURM allocation 9952061 with Harbor 0.21.0 and
cached Terminal-Bench 2.1 SIFs. All runs used `--install-only`; agent execution
and verification were absent.

## Artifact

- Claude Code: `2.1.233`
- SHA-256: `55d281096f57d411ebbdd94dbf5e9ff3accb7c05713e37348c2c11d4b83bf9d9`
- Host path: `/u/yuchen85/scratch/claude-code-artifacts/claude-code-2.1.233-linux-x86_64`
- Configurable container path: `$HOME/.local/share/agentdebugx/claude/2.1.233/claude`
- Harbor-compatible launcher: `$HOME/.local/bin/claude`
- Shared config: `examples/terminal_bench_eval/claude_installer.yaml`

## Results

- Known-good smoke, `sqlite-db-truncate`: success, 0.915 seconds.
- Final prior-APT-failure smoke, `break-filter-js-from-html`: success, 0.902 seconds.
- YAML/custom-path smoke, `break-filter-js-from-html`: success, 0.899 seconds.
- Resolved-YAML snapshot smoke, `break-filter-js-from-html`: success, 0.891 seconds.
- Full cached matrix: 15 successful installs out of 17 rows.
- Successful install times: 0.905–1.362 seconds.
- Artifact compatibility failures: 0.
- Agent installation failures: 0.
- Image start/conversion failures: 2.

The two image-start failures were `adaptive-rejection-sampler` and `regex-log`.
Both cached SIFs lack Python. Harbor installed Python during its own environment
bootstrap, then failed all three server-start attempts with `FATAL: cannot
bootstrap pip`. Both results have `agent_setup: null`, so the pinned installer
was never invoked. Per the project boundary, Harbor was not modified.

Four of the six images that previously failed Harbor's APT-based Claude setup
now reach the pinned version: `break-filter-js-from-html`, `build-cython-ext`,
`multi-source-data-merger`, and `polyglot-c-py`. The other two are the
pre-agent image-start failures above.

## Artifacts

- Full Harbor job: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-15-24`
- Matrix JSONL: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-15-24/install-matrix.jsonl`
- Known-good smoke: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-09-16`
- Final prior-APT-failure smoke: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-27-06`
- YAML/custom-path smoke: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-35-23`
- Resolved-YAML snapshot smoke: `/u/yuchen85/scratch/harbor-jobs/2026-08-15__18-41-39`
