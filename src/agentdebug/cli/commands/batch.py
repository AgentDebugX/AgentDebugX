"""Batch command workflows."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from typing import Any

from agentdebug.batch import run_batch_diagnose, run_batch_ingest
from agentdebug.cli import legacy
from agentdebug.schema import AgentTrajectory, DiagnosticReport


def run(args: Namespace) -> int:
    try:
        if args.batch_command == 'ingest':
            summary = run_batch_ingest(
                args.input,
                args.out_dir,
                format=args.format,
                goal=args.goal,
                framework=args.framework,
            )
        else:
            summary = _run_diagnose(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'batch {args.batch_command} failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(summary.to_dict(), indent=2))
    return 0 if summary.failed == 0 else 3


def _run_diagnose(args: Namespace):
    diagnose_mode = legacy._normalize_choice(
        args.diagnose_mode or 'heuristic',
        legacy._DIAGNOSE_MODE_ALIASES,
        'diagnose mode',
    )
    attributor_mode = legacy._normalize_choice(
        args.attributor_mode or 'none',
        legacy._ATTRIBUTOR_ALIASES,
        'attributor',
    )
    recovery_mode = (
        'deepdebug'
        if args.recovery_mode == 'auto' and diagnose_mode == 'deep'
        else 'none'
        if args.recovery_mode == 'auto'
        else legacy._normalize_choice(
            args.recovery_mode or 'none',
            legacy._RECOVERY_ALIASES,
            'recovery mode',
        )
    )
    needs_llm = (
        diagnose_mode in {'judge', 'deep', 'gui-rca'}
        or attributor_mode in legacy._LLM_ATTRIBUTORS
        or recovery_mode in legacy._LLM_RECOVERIES
    )
    llm: Any = None
    if needs_llm:
        llm = legacy._build_llm(args, command_name='batch diagnose')
        if llm is None:
            raise ValueError('LLM configuration is required')

    def diagnose(trajectory: AgentTrajectory) -> DiagnosticReport:
        return legacy._run_diagnose_pipeline(
            args,
            trajectory,
            diagnose_mode=diagnose_mode,
            attributor_mode=attributor_mode,
            recovery_mode=recovery_mode,
            llm=llm,
        )

    return run_batch_diagnose(
        args.trajectory,
        args.out_dir,
        diagnose,
        format=args.format,
        goal=args.goal,
        framework=args.framework,
    )


__all__ = ['run']
