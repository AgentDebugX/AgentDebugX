# 01_diagnose_raw — PASS

**Intent:** trigger + diagnose raw trace directly + faithful reporting
**Prompt:** Here is a raw agent log at trace.json. Why did this agent fail?

## Commands Claude ran (in order)

1. `agentdebug diagnose /tmp/tmp.pnoiIC0dad/trace.json --suggest --traceback 2>&1`

## Assertions

- ✓ `skill_fires`
- ✓ `must_run: agentdebug (diagnose|analyze|ingest|convert)\b`

## Answer excerpt (first 600 chars)

```
Here is the diagnosis:

## Candidate Root Cause

**Premature stop at step 3** (`evt_944eb84cf2c347e4a91e74fb2a2ebed8`, `verification.premature_stop`)

The agent was given a WebShop task — find men's dress shirts matching a complex set of filters (machine wash, polyester heathers, classic fit, black, size small, under $50) — but terminated early at **step 3** rather than fully exploring the product catalog. This happened three more times (steps 19, 27, 55), compounding into a cascade.

The terminal failure at **step 59** (`planning.inefficient_plan`) is a consequence: after the repeated prematu
```
