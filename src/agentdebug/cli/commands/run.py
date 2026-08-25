"""Unified single-trajectory debug-run command."""

from __future__ import annotations

import json
from typing import Any

from agentdebug.workbench.models import RunRequest
from agentdebug.workbench.service import execute_run


def run(args: Any) -> int:
    store_type = 'jsonl' if args.store_jsonl else 'sqlite'
    store_path = args.store_jsonl or args.store_sqlite or '.agentdebug/agentdebug.sqlite'
    try:
        result = execute_run(RunRequest(
            input_reference=args.input, profile=args.profile,
            format_override=args.format_override,
            diagnoser_override=args.diagnoser, attributor_override=args.attributor,
            recovery_override=args.recovery, store_type=store_type,
            store_path=store_path, run_root=args.run_root,
            plan_only=args.plan, ui=args.ui,
        ))
    except ValueError as exc:
        print(f'run failed: {exc}', file=__import__('sys').stderr)
        return 2
    payload = result.model_dump(mode='json') if hasattr(result, 'model_dump') else json.loads(result.json())
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"run {result.run_id}: {result.status}")
        print(f"pipeline: {result.resolved_pipeline.profile} / {result.resolved_pipeline.diagnoser.value}")
        if result.candidate_root_cause:
            print(f"candidate root cause: {result.candidate_root_cause.get('summary')}")
        if result.ui_url:
            print(f"UI: {result.ui_url}")
    if result.status in {'completed', 'planned'}:
        return 5 if args.ui and any(w.code == 'ui_unavailable' for w in result.warnings) else 0
    if any(e.code in {'invalid_input', 'incompatible_pipeline'} for e in result.errors):
        return 2
    return 4 if any(e.code == 'llm_unavailable' for e in result.errors) else 3


__all__ = ['run']
