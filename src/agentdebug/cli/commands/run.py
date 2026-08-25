"""Unified debug-run command for one trajectory or an explicit batch."""

from __future__ import annotations

import json
from typing import Any

from agentdebug.workbench.models import RunRequest
from agentdebug.workbench.service import execute_batch_run, execute_run


def run(args: Any) -> int:
    store_type = 'jsonl' if args.store_jsonl else 'sqlite'
    store_path = args.store_jsonl or args.store_sqlite or '.agentdebug/agentdebug.sqlite'
    try:
        request = RunRequest(
            input_reference=args.input, profile=args.profile,
            input_trajectory_id=args.trajectory_id,
            format_override=args.format_override,
            diagnoser_override=args.diagnoser, attributor_override=args.attributor,
            recovery_override=args.recovery, store_type=store_type,
            store_path=store_path, run_root=args.run_root,
            plan_only=args.plan, ui=args.ui,
        )
        result = execute_batch_run(request) if args.batch else execute_run(request)
    except ValueError as exc:
        print(f'run failed: {exc}', file=__import__('sys').stderr)
        return 2
    payload = result.model_dump(mode='json') if hasattr(result, 'model_dump') else json.loads(result.json())
    if args.json:
        print(json.dumps(payload))
    else:
        if args.batch:
            print(
                f'batch run: {result.status} '
                f'({result.succeeded}/{result.total} succeeded)'
            )
            for item in result.items:
                run_id = item.result.run_id if item.result else '-'
                print(f'{item.record_id}: {item.status} [{run_id}]')
        else:
            print(f"run {result.run_id}: {result.status}")
            print(f"pipeline: {result.resolved_pipeline.profile} / {result.resolved_pipeline.diagnoser.value}")
            if result.candidate_root_cause:
                print(f"candidate root cause: {result.candidate_root_cause.get('summary')}")
            if result.ui_url:
                print(f"UI: {result.ui_url}")
    if result.status in {'completed', 'planned'}:
        warnings = (
            [warning for item in result.items if item.result for warning in item.result.warnings]
            if args.batch else result.warnings
        )
        return 5 if args.ui and any(w.code == 'ui_unavailable' for w in warnings) else 0
    if args.batch:
        errors = [error for item in result.items for error in item.errors]
        return 4 if result.succeeded == 0 and any(e.code == 'llm_unavailable' for e in errors) else 3
    if any(e.code in {'invalid_input', 'incompatible_pipeline'} for e in result.errors):
        return 2
    return 4 if any(e.code == 'llm_unavailable' for e in result.errors) else 3


__all__ = ['run']
