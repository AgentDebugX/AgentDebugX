# CLI reference

The installed command is `agentdebug`.

```bash
agentdebug --help
agentdebug <command> --help
```

## Primary commands

| Command | Purpose |
| --- | --- |
| `agentdebug ingest` | Normalize one external trace export |
| `agentdebug batch ingest` | Normalize a directory or independent JSONL records |
| `agentdebug diagnose` | Run detection, attribution, and recovery planning |
| `agentdebug batch diagnose` | Normalize and diagnose a collection with per-record failure isolation |
| `agentdebug rerun` | Build a plan, export actor tasks, simulate, or call a live executor |
| `agentdebug runner serve` | Expose an application callback over the live runner HTTP protocol |
| `agentdebug list` | List trace IDs from a SQLite or JSONL store |
| `agentdebug show` | Print one stored trajectory |
| `agentdebug config` | Manage LLM endpoints and persistent runner configuration |
| `agentdebug serve` | Start the optional local inspection UI |
| `agentdebug inspect` | Compatibility name for `serve` |
| `agentdebug doctor` | Report adapter and integration availability |
| `agentdebug hub` | Package, scrub, push, or pull Error Hub bundles |
| `agentdebug integrations` | Generate host-runtime integration assets |

Compatibility commands remain available:

- `agentdebug analyze` is the heuristic-compatible diagnosis entry point.
- `agentdebug convert` aliases `agentdebug ingest`.
- `agentdebug act` contains compatibility namespaces for advanced actions.

## `ingest`

```text
agentdebug ingest INPUT
  [--out PATH]
  [--format FORMAT]
  [--trace-id ID]
  [--task-id ID]
  [--goal TEXT]
  [--framework NAME]
```

Use [Ingest traces](../guides/ingest.md) for supported format names and examples.

## `diagnose`

```text
agentdebug diagnose TRAJECTORY
  [--mode MODE]
  [--attributor [ATTRIBUTOR]]
  [--recovery RECOVERY]
  [--model MODEL]
  [--base-url URL]
  [--api-key KEY]
  [--embedding-model MODEL]
  [--embedding MODEL]
  [--rule-pack PACK]
  [--out PATH]
  [--traceback]
  [--no-color]
```

`--store-sqlite` and `--store-jsonl` let `TRAJECTORY` refer to a stored trace ID. The two store options are mutually exclusive.

Use [Diagnose failures](../guides/diagnose.md) for mode and component semantics.

## `rerun`

```text
agentdebug rerun DIAGNOSTIC_REPORT
  [--trajectory TRAJECTORY]
  [--start-event N]
  [--runner NAME]
  [--runner-command COMMAND]
  [--runner-cwd PATH]
  [--runner-timeout SECONDS]
  [--simulate]
  [--plan-only]
  [--actor-task-format jsonl|parquet]
  [--out PATH]
```

`--start-event` is 1-based. Planning, simulation, and live execution are intentionally distinct. See [Validate with Rerun](../guides/rerun.md).

## `serve`

Exactly one store is required:

```text
agentdebug serve
  (--store-sqlite PATH | --store-jsonl PATH)
  [--host HOST]
  [--port PORT]
```

The UI dependencies come from `agentdebugx[ui]`.

## Configuration safety

Prefer saved configuration or environment variables over repeating API keys on the command line. `agentdebug config show` masks stored secrets. Use `agentdebug config --help` for the current configuration subcommands.

!!! note "The runtime help is authoritative"

    This page explains the stable command surface. Run `agentdebug <command> --help` for the exact flags accepted by the installed version.
