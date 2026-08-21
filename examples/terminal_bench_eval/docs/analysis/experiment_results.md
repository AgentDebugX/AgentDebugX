# Terminal-Bench experiment results

Keep derived experiment tables and analysis in this directory. Each result
should identify the exact Harbor artifacts, fixed configuration, denominator,
and infrastructure exclusions. Do not combine oracle qualification, agent
outcomes, installer validation, or recovery-method results into one metric.

## Oracle qualification sweep, 2026-08-20

The aggregate Harbor job
`/scratch/yuchen85/harbor-jobs/2026-08-20__19-39-18` ran the Oracle over all
26 tasks in the candidate set. It used Harbor 0.21.0 and the Singularity
backend. The raw Harbor result was **17 resolved, 8 unresolved, and 1
errored**, with no retries.

This is an environment-qualification run, not an agent evaluation. Under the
outcome rules in `../EXPERIMENT_PROTOCOL.md`, only the 17 Oracle-resolved tasks
are qualified for agent experiments:

| Oracle-qualified task | Oracle reward |
|---|---:|
| `break-filter-js-from-html` | 1.0 |
| `cancel-async-tasks` | 1.0 |
| `circuit-fibsqrt` | 1.0 |
| `cobol-modernization` | 1.0 |
| `code-from-image` | 1.0 |
| `db-wal-recovery` | 1.0 |
| `distribution-search` | 1.0 |
| `feal-linear-cryptanalysis` | 1.0 |
| `fix-code-vulnerability` | 1.0 |
| `git-leak-recovery` | 1.0 |
| `kv-store-grpc` | 1.0 |
| `large-scale-text-editing` | 1.0 |
| `openssl-selfsigned-cert` | 1.0 |
| `path-tracing-reverse` | 1.0 |
| `raman-fitting` | 1.0 |
| `sqlite-db-truncate` | 1.0 |
| `write-compressor` | 1.0 |

The other nine tasks are Oracle-invalid in this environment and must not be
counted as agent failures:

| Task | Raw result | Observed cause | Classification |
|---|---:|---|---|
| `constraints-scheduling` | unresolved | `alice_calendar.ics` is a directory instead of a file; the Oracle and verifier both raise `IsADirectoryError`. | task packaging / filesystem failure |
| `count-dataset-tokens` | unresolved | Package downloads and installation complete, but `/app/count_tokens.py` cannot import `datasets`. | Python environment mismatch, not a download failure |
| `fix-git` | unresolved | Git cannot update `ORIG_HEAD`: the reference cannot be resolved and locking it returns `Invalid argument`. | Git-ref / filesystem incompatibility |
| `log-summary-date-ranges` | unresolved | Expected log paths return `Not a directory`; the generated CSV has 12 of the required 15 rows. | task packaging / filesystem failure |
| `multi-source-data-merger` | unresolved | The Oracle stops immediately because `pandas` is unavailable, so neither required output file is created. | missing task bootstrap dependency |
| `polyglot-c-py` | unresolved | The verifier cannot install or invoke its tools: `curl` and `uvx` are unavailable. | verifier infrastructure failure |
| `sanitize-git-repo` | unresolved | Two verifier checks pass, but the Oracle changes `baselines/README.md`, which the third check forbids. | reference-solution / verifier mismatch |
| `build-cython-ext` | unresolved | Ten of eleven verifier checks pass; the remaining check clones pyknotid 0.5.3 and its external repository test suite exits nonzero. | external verifier dependency / compatibility failure |
| `sqlite-with-gcov` | errored | Oracle execution times out after 900 seconds. Verifier setup also hits cross-device `dpkg` errors, a broken `curl`, and missing `uvx`. | infrastructure failure |

The aggregate `result.json` is the source for the 26-task totals and raw
rewards. Each trial directory beneath the job contains `agent/oracle.txt`,
`verifier/test-stdout.txt`, `verifier/ctrf.json`, and `result.json`, which are
the sources for the diagnoses above. These results do not support a general
claim that Apptainer cannot download Python or packages: downloads succeeded
in several failed trials, while later interpreter, filesystem, bootstrap, or
verifier operations failed.

## Claude Seed sweep, 2026-08-16--17

This table reconstructs the 11-task Seed sweep from 11 separate Harbor job
directories. It was not emitted as one aggregate job. All trials used Harbor
0.21.0, `anthropic/claude-sonnet-5`, reasoning effort `medium`, the Singularity
backend, and pinned Claude Code 2.1.233.

Under the outcome rules in `../EXPERIMENT_PROTOCOL.md`, the result is:

- 11 attempted tasks;
- 8 valid agent attempts: 6 resolved and 2 unresolved (75% resolved); and
- 3 infrastructure exclusions caused by agent-execution timeouts.

The timeout exclusions are not Python-bootstrap failures. All 11 trials
completed environment and agent setup, installed the pinned Claude artifact,
started agent execution, produced a native Claude session, and recorded token
usage. The three timeout tracebacks instead contain a 600-second Singularity
HTTP request timeout. No trial in this sweep contains `cannot bootstrap pip` or
`server died`.

| Task | Raw reward | Exception | Protocol outcome | Harbor job / trial |
|---|---:|---|---|---|
| `sqlite-db-truncate` | 1.0 | none | resolved | `2026-08-16__16-28-20/sqlite-db-truncate__MYhpW3j` |
| `cancel-async-tasks` | 1.0 | none | resolved | `2026-08-16__16-31-16/cancel-async-tasks__98riuFi` |
| `raman-fitting` | 0.0 | none | unresolved agent attempt | `2026-08-16__16-34-05/raman-fitting__a6Bqv7g` |
| `code-from-image` | 1.0 | none | resolved | `2026-08-17__00-28-35/code-from-image__naXeKCY` |
| `kv-store-grpc` | 1.0 | none | resolved | `2026-08-17__00-29-15/kv-store-grpc__eGtNpM9` |
| `openssl-selfsigned-cert` | 1.0 | none | resolved | `2026-08-17__00-30-29/openssl-selfsigned-cert__4jE4K6G` |
| `circuit-fibsqrt` | 1.0 | `AgentTimeoutError` | infrastructure exclusion | `2026-08-17__00-31-49/circuit-fibsqrt__eRREjbk` |
| `db-wal-recovery` | 0.0 | none | unresolved agent attempt | `2026-08-17__00-42-28/db-wal-recovery__7vfoe5c` |
| `distribution-search` | 1.0 | none | resolved | `2026-08-17__00-44-46/distribution-search__P8dzHmh` |
| `feal-linear-cryptanalysis` | 0.0 | `AgentTimeoutError` | infrastructure exclusion | `2026-08-17__00-47-46/feal-linear-cryptanalysis__uP28De9` |
| `path-tracing-reverse` | 0.0 | `AgentTimeoutError` | infrastructure exclusion | `2026-08-17__00-58-36/path-tracing-reverse__BeinUMk` |

The job/trial paths above are relative to
`/u/yuchen85/scratch/harbor-jobs/`. Each trial's `result.json` is the source
for reward, exception, configuration, phase timing, and token counts;
`agent/claude-install.json` records the successful pinned installer result.

The separate Oracle qualification run is
`/u/yuchen85/scratch/harbor-jobs/2026-08-16__01-21-20`: all 11 tasks received
reward 1.0 with no exception. The Python-bootstrap failures documented in
`../installer_findings.md` affected `adaptive-rejection-sampler` and
`regex-log`, neither of which is in this Seed sweep.
