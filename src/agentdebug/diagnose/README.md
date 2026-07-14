# Diagnose Workflow

Diagnose is the first major phase of AgentDebugX. It converts an agent
trajectory into structured findings and recovery suggestions through three
ordered stages:

```text
Detect -> Attribute -> Recover
```

## When to use

Use Diagnose when a completed or partially completed agent run needs failure
analysis. The output should be portable: it can be inspected by humans, stored
in the Error Hub, or passed into Rerun.

## Flow

1. Detect identifies visible failure signals in events or full trajectories.
2. Attribute maps those signals to likely root causes.
3. `DiagnoseContext` preserves the detector findings and promotes the primary
   attribution to the recovery target.
4. Recover proposes repair strategies, prompts, rollback points, or rerun
   directives from that target.
5. `pipeline.py` coordinates local default execution across the three stages.

## Components

Diagnose components are registered through `diagnose/registry.py`.

Component metadata is manifest-backed and includes:

- `id`
- `stage`
- `name`
- `description`
- `entrypoint`
- `capabilities`
- `dependencies`
- `cost_profile`
- `enabled_by_default`

The registry exposes:

- `list_components(stage=None)`
- `available_components(stage=None)`
- `get_component_metadata(component_id)`
- `load_component(component_id)`
- `is_component_available(component_id)`

## Dependencies

The default local Diagnose path is dependency-light and deterministic. LLM,
GUI, and benchmark-specific components may require optional extras or external
API configuration.

## Extension Rules

- Put failure detection in `detect/`.
- Put root-cause analysis in `attribute/`.
- Put repair strategy generation in `recover/`.
- Register new components with a manifest under `component_manifests/<stage>/`.
- Keep legacy paths such as `diagnose/actions` and `diagnose/rules` as shims.
