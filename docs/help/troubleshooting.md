# Troubleshooting

## `agentdebug` is not found

Confirm that the package was installed into the active Python environment:

```bash
python -m pip show agentdebugx
python -m pip install agentdebugx
```

The install name is `agentdebugx`; the command is `agentdebug`.

## An optional integration cannot be imported

Run:

```bash
agentdebug doctor
```

Then install the extra that owns the integration. Examples:

```bash
python -m pip install "agentdebugx[ui]"
python -m pip install "agentdebugx[crewai]"
python -m pip install "agentdebugx[openai-agents]"
```

## `python -m agentdebug.gui` reports a missing package

The GUI batch application needs the memory and application layers:

```bash
python -m pip install "agentdebugx[gui,gui-memory,gui-app]"
```

Core GUI RCA and `import agentdebug.gui` intentionally have a smaller dependency boundary.

## LLM diagnosis cannot connect

Inspect masked configuration and test the endpoint:

```bash
agentdebug config show
agentdebug config doctor
```

Check that the base URL is an OpenAI-compatible API root expected by the configured client, the model exists on that endpoint, and the API key is available.

## GUI RCA cannot find screenshots

The OSWorld adapter stores screenshot paths and the resolved source directory. Check that:

1. the trajectory was imported from the original OSWorld directory,
2. `metadata.source_dir` still points to that directory,
3. screenshot files have not been moved, and
4. Pillow is installed when decoding is required.

```bash
python -m pip install "agentdebugx[gui]"
```

## A rerun plan says execution is unavailable

This is expected for a trajectory-only input. A real rerun needs an application-owned runner with the framework, model, tools, credentials, and environment state.

Create a plan to inspect the missing capabilities:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --out rerun-plan.json
```

Then configure a persistent HTTP runner or a trusted process runner as described in [Validate with Rerun](../guides/rerun.md).

## The local UI is reachable from other machines

The documentation examples bind to `127.0.0.1`. If you intentionally bind to `0.0.0.0`, place the UI behind suitable authentication and transport security. The UI itself is designed as a local surface.

## A batch exits with status 3

Status `3` means the batch partially failed. Successful records are retained. Inspect `batch-summary.json` and the per-record outputs to identify isolated invalid inputs.

## Get exact version-specific flags

```bash
agentdebug --help
agentdebug ingest --help
agentdebug diagnose --help
agentdebug rerun --help
```
