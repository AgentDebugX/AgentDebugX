"""Unified debug-run command for one trajectory or an explicit batch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentdebug.capture.hosts.registry import resolve_current_capture_context
from agentdebug.workbench.models import RunRequest
from agentdebug.workbench.service import execute_batch_run, execute_run


def run(args: Any) -> int:
    try:
        input_reference, store_type, store_path, run_root = _resolve_input(args)
        request = RunRequest(
            input_reference=input_reference, profile=args.profile,
            input_trajectory_id=args.trajectory_id,
            format_override=args.format_override,
            diagnoser_override=args.diagnoser, attributor_override=args.attributor,
            recovery_override=args.recovery, store_type=store_type,
            store_path=store_path, run_root=run_root,
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


def _resolve_input(args: Any) -> tuple[str, str, str, str]:
    use_current = bool(args.current or args.input is None)
    if args.current and args.input is not None:
        raise ValueError('--current cannot be combined with an input')
    if use_current:
        if args.batch:
            raise ValueError('--current cannot be combined with --batch')
        if args.trajectory_id:
            raise ValueError('--current cannot be combined with --trajectory-id')
        if args.store_jsonl:
            raise ValueError(
                'current captured sessions require their configured SQLite store'
            )
        context = resolve_current_capture_context()
        configured_store = str(context.store_path)
        if args.store_sqlite:
            requested_store = str(Path(args.store_sqlite).expanduser().resolve())
            if requested_store != configured_store:
                raise ValueError(
                    '--store-sqlite does not match the current capture context'
                )
        run_root = args.run_root or str(context.project_root / '.agentdebug')
        return context.trace_id, 'sqlite', configured_store, run_root
    store_type = 'jsonl' if args.store_jsonl else 'sqlite'
    store_path = args.store_jsonl or args.store_sqlite or '.agentdebug/agentdebug.sqlite'
    return str(args.input), store_type, store_path, args.run_root or '.agentdebug'


__all__ = ['run']
