# Setup Guide

Use this guide before first use, when `agentdebug` is not found, or when an
LLM-backed method exits because credentials are missing. Do not install
packages, persist secrets, or edit `.env` files without user approval.

## Preflight

Check whether AgentDebugX is already available:

```bash
command -v agentdebug
agentdebug doctor
```

If working from an AgentDebugX source checkout instead of an installed package:

```bash
PYTHONPATH=src python -m agentdebug.cli doctor
```

If `agentdebug doctor` works, do not reinstall. Continue with conversion or
diagnosis.

## Install

If `agentdebug` is missing, ask before installing. The normal package is:

```bash
python -m pip install agentdebugx
```

Optional extras:

```bash
python -m pip install 'agentdebugx[ui]'      # local dashboard
python -m pip install 'agentdebugx[all]'     # broad optional integrations
```

After install:

```bash
agentdebug doctor
agentdebug integrations skill --platform claude --target ~/.claude/skills --name agentdebug
```

Use the platform target the user requested; do not install into another host's
skill directory without approval.

## LLM Configuration

Deterministic conversion and heuristic diagnosis do not need an API key. These
do need LLM credentials:

- `--mode judge`
- `--mode deep`
- `--mode gui-rca`
- LLM attributors: `all-at-once`, `step-by-step`, `binary-search`,
  `counterfactual`
- `--recovery self-refine`

DeepDebug (`--mode deep` or `--mode deepdebug`) already includes attribution
and fix guidance. Invoke it with the currently required compatibility
placeholders `--attributor none --recovery none`; the separately listed LLM
attributors and Self-Refine recovery are for regular diagnosis modes.

If an LLM-backed command fails with missing credentials, explain that the local
heuristic report still ran if it did, then offer one of these setup paths.

### Option A: One-off Flags

Use when the user does not want to persist secrets:

```bash
agentdebug diagnose .agentdebug/<case>.trajectory.json \
  --mode judge \
  --attributor all-at-once \
  --recovery critic \
  --base-url "$AGENTDEBUG_LLM_BASE_URL" \
  --api-key "$AGENTDEBUG_LLM_API_KEY" \
  --model "$AGENTDEBUG_LLM_MODEL"
```

### Option B: Environment Variables

Set these in the current shell before running LLM-backed commands:

```bash
export AGENTDEBUG_LLM_BASE_URL="https://<openai-compatible-host>/v1"
export AGENTDEBUG_LLM_API_KEY="<secret>"
export AGENTDEBUG_LLM_MODEL="gemini-3-flash"
export AGENTDEBUG_LLM_EMBEDDING_MODEL="text-embedding-3-small"  # optional
```

Then verify:

```bash
agentdebug config doctor
```

### Option C: Saved AgentDebugX Config

Use when the user wants AgentDebugX to remember the endpoint. This writes
`~/.agentdebug/config.json` by default with mode `0600`. The path can be
overridden with `AGENTDEBUG_CONFIG`.

```bash
agentdebug config set-llm \
  --base-url "$AGENTDEBUG_LLM_BASE_URL" \
  --api-key "$AGENTDEBUG_LLM_API_KEY" \
  --model "$AGENTDEBUG_LLM_MODEL" \
  --embedding-model "$AGENTDEBUG_LLM_EMBEDDING_MODEL"

agentdebug config show
agentdebug config doctor
```

`agentdebug config show` masks secrets by default. Use `--show-secrets` only in
trusted terminals.

## `.env` Files

AgentDebugX CLI does not currently auto-load `.env` files. If the user keeps
credentials in `.env`, use one of these approaches:

1. Ask the user to export the variables in their shell, then run AgentDebugX.
2. If the user approves reading `.env`, load only the `AGENTDEBUG_LLM_*` keys
   into the current process and do not print the API key.
3. Persist them with `agentdebug config set-llm` only if the user approves
   storing secrets in AgentDebugX config.

Avoid `source .env` on untrusted files because shell syntax can execute code.
Prefer a safe key-value parser or ask the user to run exports themselves.

## Failure Handling

If `diagnose --mode judge` or `diagnose --mode deep` exits `4`, report:

- the deterministic diagnosis status, if already run,
- that LLM credentials are missing or incomplete,
- the exact accepted configuration paths: flags, `AGENTDEBUG_LLM_*`, or
  `agentdebug config set-llm`,
- the command to retry after setup.

Do not fabricate an LLM-backed diagnosis when credentials are missing.
