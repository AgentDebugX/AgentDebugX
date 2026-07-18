"""Command line entry points.

Primary user flow:

* ``agentdebug ingest``   - normalize external traces into ``AgentTrajectory``
* ``agentdebug diagnose`` - run diagnose + attribution + recovery planning
* ``agentdebug rerun``    - execute a second-stage model rollout from a report
* ``agentdebug inspect``  - inspect traces through local UI / store commands

Compatibility aliases remain available for ``convert``, ``analyze``,
``serve``, ``hub``, ``list``, ``show``, ``doctor``, and ``integrations``.
Judge, attribution, recovery, and DeepDebug are selected through ``diagnose``
options so there is only one diagnosis pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from agentdebug.analyzers import HeuristicAnalyzer
from agentdebug.attribution import (
    AllAtOnceAttributor,
    BinarySearchAttributor,
    CounterfactualAttributor,
    HeuristicAttributor,
    StepByStepAttributor,
)
from agentdebug.models import (
    AgentTrajectory,
    DiagnosticReport,
    model_to_json,
    trajectory_from_json,
)
from agentdebug.recovery import (
    AutoManualRules,
    Compensator,
    CriticRecoverer,
    DeepDebugRecovery,
    FixProposal,
    ReflexionSuggestion,
    SagaRollback,
    SelfRefineLoop,
)
from agentdebug.rerun import RerunWorkflow
from agentdebug.storage import JsonlTraceStore, SQLiteTraceStore, TraceStore


_TRACE_FORMAT_CHOICES = [
    'auto',
    'agenttrajectory',
    'messages',
    'message_list',
    'conversations',
    'event_list',
    'webshop_pages',
    'openai_agents_spans',
    'crewai_events',
    'langgraph_callbacks',
    'openclaw',
    'claude_code',
    'hermes',
    'osworld',
]

_DIAGNOSE_MODE_ALIASES = {
    'rules': 'heuristic',
    'rule': 'heuristic',
    'heuristic': 'heuristic',
    'deterministic': 'heuristic',
    'judge': 'judge',
    'llm': 'judge',
    'llm-judge': 'judge',
    'llm_judge': 'judge',
    'deep': 'deep',
    'deepdebug': 'deep',
    'deep-debug': 'deep',
    'deep_debug': 'deep',
    'gui-rca': 'gui-rca',
    'gui_rca': 'gui-rca',
    'gui': 'gui-rca',
}

_ATTRIBUTOR_ALIASES = {
    'none': 'none',
    'off': 'none',
    'false': 'none',
    'heuristic': 'heuristic',
    'HeuristicAttributor': 'heuristic',
    'all-at-once': 'all_at_once',
    'all_at_once': 'all_at_once',
    'allatonce': 'all_at_once',
    'AllAtOnceAttributor': 'all_at_once',
    'step-by-step': 'step_by_step',
    'step_by_step': 'step_by_step',
    'stepbystep': 'step_by_step',
    'StepByStepAttributor': 'step_by_step',
    'binary-search': 'binary_search',
    'binary_search': 'binary_search',
    'binarysearch': 'binary_search',
    'BinarySearchAttributor': 'binary_search',
    'counterfactual': 'counterfactual',
    'CounterfactualAttributor': 'counterfactual',
}

_LLM_ATTRIBUTORS = {
    'all_at_once',
    'step_by_step',
    'binary_search',
    'counterfactual',
}

_RECOVERY_ALIASES = {
    'none': 'none',
    'off': 'none',
    'false': 'none',
    'deep': 'deepdebug',
    'deep-debug': 'deepdebug',
    'deep_debug': 'deepdebug',
    'deepdebug': 'deepdebug',
    'DeepDebugRecovery': 'deepdebug',
    'reflexion': 'reflexion',
    'ReflexionSuggestion': 'reflexion',
    'critic': 'critic',
    'CriticRecoverer': 'critic',
    'self-refine': 'self_refine',
    'self_refine': 'self_refine',
    'selfrefine': 'self_refine',
    'SelfRefineLoop': 'self_refine',
    'auto-manual': 'auto_manual',
    'auto_manual': 'auto_manual',
    'automanual': 'auto_manual',
    'AutoManualRules': 'auto_manual',
    'saga-rollback': 'saga_rollback',
    'saga_rollback': 'saga_rollback',
    'sagarollback': 'saga_rollback',
    'SagaRollback': 'saga_rollback',
}

_LLM_RECOVERIES = {'self_refine'}

_ATTRIBUTOR_CLASS_FLAGS = {
    '--AllAtOnceAttributor': 'AllAtOnceAttributor',
    '--StepByStepAttributor': 'StepByStepAttributor',
    '--BinarySearchAttributor': 'BinarySearchAttributor',
}

_RECOVERY_CLASS_FLAGS = {
    '--DeepDebugRecovery': 'DeepDebugRecovery',
    '--ReflexionSuggestion': 'ReflexionSuggestion',
    '--CriticRecoverer': 'CriticRecoverer',
    '--SelfRefineLoop': 'SelfRefineLoop',
    '--AutoManualRules': 'AutoManualRules',
    '--SagaRollback': 'SagaRollback',
}


def _add_diagnose_args(
    parser: argparse.ArgumentParser,
    *,
    trajectory_help: str,
    require_pipeline: bool = False,
) -> None:
    parser.add_argument('trajectory', help=trajectory_help)
    _add_store_args(parser, required=False)
    parser.add_argument(
        '--mode',
        '--diagnoser',
        dest='diagnose_mode',
        default=None if require_pipeline else 'heuristic',
        choices=sorted(_DIAGNOSE_MODE_ALIASES),
        help=(
            'Diagnosis engine: heuristic/rules, judge/llm, deep/deepdebug, or '
            'gui-rca (OSWorld GUI root-cause; requires a tool-calling + '
            'vision-capable LLM backend). '
            'Required for diagnose; analyze defaults to heuristic.'
        ),
    )
    parser.add_argument(
        '--judge',
        action='store_const',
        const='judge',
        dest='diagnose_mode',
        help='Shortcut for --mode judge.',
    )
    parser.add_argument(
        '--deep',
        action='store_const',
        const='deep',
        dest='diagnose_mode',
        help='Shortcut for --mode deep.',
    )
    parser.add_argument(
        '--attributor',
        '--attribute',
        dest='attributor_mode',
        default=None if require_pipeline else 'none',
        nargs='?',
        const='all-at-once',
        choices=sorted(_ATTRIBUTOR_ALIASES),
        help=(
            'Optional blame localization backend to attach under report.attribution: '
            'heuristic, all-at-once, step-by-step, binary-search, or counterfactual.'
        ),
    )
    _add_hidden_const_flags(parser, dest='attributor_mode', flags=_ATTRIBUTOR_CLASS_FLAGS)
    parser.add_argument(
        '--recovery',
        '--recoverer',
        dest='recovery_mode',
        default=None if require_pipeline else 'auto',
        choices=sorted(_RECOVERY_ALIASES),
        help=(
            'Recovery prompt writer to attach under report.recovery: deepdebug, '
            'reflexion, critic, self-refine, auto-manual, or saga-rollback. '
            'Defaults to deepdebug for --mode deepdebug and none otherwise.'
        ),
    )
    _add_hidden_const_flags(parser, dest='recovery_mode', flags=_RECOVERY_CLASS_FLAGS)
    _add_llm_args(parser)
    parser.add_argument(
        '--embedding',
        dest='embedding_model',
        help='Optional embedding model for deep-memory retrieval.',
    )
    parser.add_argument('--out', help='Optional output path for the report')
    parser.add_argument(
        '--suggest',
        action='store_true',
        help='Legacy alias: append Reflexion text outside the structured report',
    )
    parser.add_argument(
        '--traceback',
        action='store_true',
        help='Render a Python-traceback-style cascade view instead of JSON',
    )
    parser.add_argument(
        '--no-color',
        action='store_true',
        help='Disable ANSI colors in --traceback output (default: auto)',
    )
    parser.add_argument(
        '--rule-pack',
        action='append',
        choices=['auto', 'core', 'agenterrorbench', 'gui', 'all'],
        help=(
            'Rule pack to load for heuristic analysis. Repeatable. '
            'Default: auto (core plus detected benchmark packs).'
        ),
    )


def _add_batch_diagnose_args(parser: argparse.ArgumentParser) -> None:
    """Add only pipeline controls that are meaningful for batch diagnosis."""

    parser.add_argument('trajectory', help='Directory, JSON file, or JSONL file')
    parser.add_argument(
        '--mode',
        '--diagnoser',
        dest='diagnose_mode',
        default='heuristic',
        choices=sorted(_DIAGNOSE_MODE_ALIASES),
    )
    parser.add_argument(
        '--attributor',
        '--attribute',
        dest='attributor_mode',
        default='none',
        nargs='?',
        const='all-at-once',
        choices=sorted(_ATTRIBUTOR_ALIASES),
    )
    _add_hidden_const_flags(
        parser, dest='attributor_mode', flags=_ATTRIBUTOR_CLASS_FLAGS
    )
    parser.add_argument(
        '--recovery',
        '--recoverer',
        dest='recovery_mode',
        default='auto',
        choices=sorted(_RECOVERY_ALIASES),
    )
    _add_hidden_const_flags(
        parser, dest='recovery_mode', flags=_RECOVERY_CLASS_FLAGS
    )
    _add_llm_args(parser)
    parser.add_argument('--embedding', dest='embedding_model')
    parser.add_argument(
        '--rule-pack',
        action='append',
        choices=['auto', 'core', 'agenterrorbench', 'gui', 'all'],
    )


def _add_ingest_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('input', help='Path to a JSON or JSONL trace export')
    parser.add_argument('--out', help='Optional output path for converted JSON')
    parser.add_argument(
        '--format',
        default='auto',
        choices=_TRACE_FORMAT_CHOICES,
        help='Input format. Defaults to auto-detection.',
    )
    parser.add_argument('--trace-id', dest='trace_id')
    parser.add_argument('--task-id', dest='task_id')
    parser.add_argument('--goal')
    parser.add_argument('--framework')


def _add_rerun_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('diagnostic_report', help='Path to a diagnose report JSON')
    parser.add_argument(
        '--trajectory',
        help='Original trajectory path or trace_id used for rerun context',
    )
    parser.add_argument(
        '--start-event',
        type=int,
        metavar='N',
        help=(
            'Start from the Nth event in --trajectory (1-based). '
            'Without this option, rerun starts from the beginning.'
        ),
    )
    _add_store_args(parser, required=False)
    _add_llm_args(parser)
    parser.add_argument(
        '--runner-command',
        help=(
            'Trusted framework runner command for live model/tool execution. '
            'Defaults to AGENTDEBUG_RERUN_COMMAND.'
        ),
    )
    parser.add_argument(
        '--runner',
        help='Named persistent HTTP runner saved by `agentdebug config set-runner`.',
    )
    parser.add_argument(
        '--runner-cwd',
        help='Working directory for the live framework runner.',
    )
    parser.add_argument(
        '--runner-timeout',
        type=float,
        default=1800.0,
        help='Live runner timeout in seconds (default: 1800).',
    )
    parser.add_argument(
        '--simulate',
        action='store_true',
        help=(
            'Explicitly allow an LLM-generated simulated rollout. This does '
            'not execute tools and is never treated as live execution.'
        ),
    )
    parser.add_argument(
        '--plan-only',
        action='store_true',
        help='Build the rerun request without calling the configured model.',
    )
    parser.add_argument(
        '--actor-task-format',
        choices=['jsonl', 'parquet'],
        help=(
            'With --plan-only, export one pending actor rollout task instead '
            'of the workflow JSON.'
        ),
    )
    parser.add_argument('--out', help='Optional output path for rerun result JSON')


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--model',
        default=None,
        help='LLM model. Defaults to CLI config, env, then gemini-3-flash.',
    )
    parser.add_argument('--base-url', dest='base_url')
    parser.add_argument('--api-key', dest='api_key')
    parser.add_argument(
        '--embedding-model',
        dest='embedding_model',
        help='Embedding model. Defaults to CLI config, env, then text-embedding-3-small.',
    )


def _add_hidden_const_flags(
    parser: argparse.ArgumentParser, *, dest: str, flags: dict[str, str]
) -> None:
    """Register legacy class-name shortcuts without cluttering --help."""
    for flag, const in flags.items():
        parser.add_argument(
            flag,
            action='store_const',
            const=const,
            dest=dest,
            help=argparse.SUPPRESS,
        )


def _add_hub_subcommands(parser: argparse.ArgumentParser) -> None:
    hub_sub = parser.add_subparsers(dest='hub_command', required=True)

    p_push = hub_sub.add_parser('push', help='Publish a trace bundle')
    p_push.add_argument('trace_id', help='Trace ID to bundle')
    p_push.add_argument(
        '--to',
        required=True,
        help='Hub spec: local:/path | git:<remote>[#<path>] | hf:<repo_id>[#<path>]',
    )
    _add_store_args(p_push, required=True)
    p_push.add_argument(
        '--no-scrub',
        action='store_true',
        help='DANGER: skip PII/secret scrubbing (only for trusted internal hubs)',
    )
    p_push.add_argument('--license', default='CC-BY-4.0')
    p_push.add_argument(
        '--contributor',
        default=None,
        help='Optional contributor identifier (email, handle)',
    )
    p_push.add_argument('--contributor-org', default=None)
    p_push.add_argument(
        '--message',
        default=None,
        help='Optional commit message (git/hf only)',
    )

    p_pull = hub_sub.add_parser('pull', help='Fetch a bundle from a Hub')
    p_pull.add_argument('spec', help='Hub spec (see hub push)')
    p_pull.add_argument('--bundle', required=True, help='Bundle ID')
    p_pull.add_argument('--into', default='.agentdebug/hub_pulls')

    p_list = hub_sub.add_parser('list', help='List bundles available at a Hub')
    p_list.add_argument('spec')
    p_list.add_argument('--limit', type=int, default=50)


def _add_integrations_subcommands(parser: argparse.ArgumentParser) -> None:
    int_sub = parser.add_subparsers(dest='int_command', required=True)

    p_skill = int_sub.add_parser('skill', help='Materialize a host debug skill')
    p_skill.add_argument(
        '--target',
        default='~/.claude/skills',
        help='Destination directory (default: ~/.claude/skills)',
    )
    p_skill.add_argument('--name', default='agentdebug')
    p_skill.add_argument(
        '--platform',
        choices=('claude', 'hermes', 'openclaw'),
        default='claude',
        help='Host skill format to generate (default: claude)',
    )

    p_mc = int_sub.add_parser(
        'openhands-microagent',
        help='Write an OpenHands microagent contract file',
    )
    p_mc.add_argument('--target', default='.openhands/microagents')
    p_mc.add_argument('--name', default='agentdebug')


def _add_config_subcommands(parser: argparse.ArgumentParser) -> None:
    config_sub = parser.add_subparsers(dest='config_command', required=True)

    p_set = config_sub.add_parser('set-llm', help='Save default LLM settings')
    p_set.add_argument('--base-url', required=True, help='OpenAI-compatible /v1 URL')
    p_set.add_argument('--api-key', required=True, help='LLM API key')
    p_set.add_argument(
        '--model',
        required=True,
        help='Default chat/completions model',
    )
    p_set.add_argument(
        '--embedding-model',
        default=None,
        help='Optional default embeddings model',
    )
    p_set.set_defaults(handler=_cmd_config)

    p_show = config_sub.add_parser('show', help='Show saved config with secrets masked')
    p_show.add_argument(
        '--show-secrets',
        action='store_true',
        help='Print API keys in clear text. Use only in trusted terminals.',
    )
    p_show.set_defaults(handler=_cmd_config)

    p_clear = config_sub.add_parser('clear-llm', help='Remove saved LLM settings')
    p_clear.set_defaults(handler=_cmd_config)

    p_doctor = config_sub.add_parser('doctor', help='Test the configured LLM endpoint')
    p_doctor.add_argument('--model', default=None, help='Override configured model')
    p_doctor.add_argument('--base-url', dest='base_url', default=None)
    p_doctor.add_argument('--api-key', dest='api_key', default=None)
    p_doctor.set_defaults(handler=_cmd_config)

    p_runner = config_sub.add_parser(
        'set-runner', help='Save a persistent HTTP agent runner'
    )
    p_runner.add_argument('name', help='Local runner name')
    p_runner.add_argument('--url', required=True, help='Runner service base URL')
    p_runner.add_argument(
        '--token-env',
        default=None,
        help='Environment variable containing the bearer token',
    )
    p_runner.add_argument('--timeout', type=float, default=1800.0)
    p_runner.add_argument('--poll-interval', type=float, default=1.0)
    p_runner.add_argument('--max-retries', type=int, default=3)
    p_runner.add_argument('--retry-delay', type=float, default=0.5)
    p_runner.add_argument(
        '--insecure', action='store_true', help='Disable TLS certificate verification'
    )
    p_runner.add_argument('--default', action='store_true')
    p_runner.set_defaults(handler=_cmd_config)

    p_list_runners = config_sub.add_parser(
        'list-runners', help='List configured HTTP runners'
    )
    p_list_runners.set_defaults(handler=_cmd_config)

    p_use_runner = config_sub.add_parser(
        'use-runner', help='Select the default HTTP runner'
    )
    p_use_runner.add_argument('name')
    p_use_runner.set_defaults(handler=_cmd_config)

    p_remove_runner = config_sub.add_parser(
        'remove-runner', help='Remove a configured HTTP runner'
    )
    p_remove_runner.add_argument('name')
    p_remove_runner.set_defaults(handler=_cmd_config)

    p_doctor_runner = config_sub.add_parser(
        'doctor-runner', help='Verify an HTTP runner and print capabilities'
    )
    p_doctor_runner.add_argument('name', nargs='?')
    p_doctor_runner.set_defaults(handler=_cmd_config)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='agentdebug')
    sub = parser.add_subparsers(dest='command', required=True)

    p_analyze = sub.add_parser(
        'analyze',
        help='Compatibility alias: heuristic-only diagnosis',
    )
    _add_diagnose_args(
        p_analyze,
        trajectory_help='Path to an AgentTrajectory JSON file',
        require_pipeline=False,
    )
    p_analyze.set_defaults(handler=_cmd_diagnose)

    p_diagnose = sub.add_parser(
        'diagnose',
        help='Run diagnosis, attribution, and recovery planning',
    )
    _add_diagnose_args(
        p_diagnose,
        trajectory_help='Path to a trajectory or external trace file',
        require_pipeline=False,
    )
    p_diagnose.set_defaults(handler=_cmd_diagnose)

    p_convert = sub.add_parser(
        'convert',
        help='Compatibility alias for ingest',
    )
    _add_ingest_args(p_convert)
    p_convert.set_defaults(handler=_cmd_convert)

    p_ingest = sub.add_parser(
        'ingest',
        help='Normalize an offline trace export into AgentTrajectory JSON',
    )
    _add_ingest_args(p_ingest)
    p_ingest.set_defaults(handler=_cmd_convert)

    p_list = sub.add_parser('list', help='List trace IDs in a store')
    _add_store_args(p_list)
    p_list.set_defaults(handler=_cmd_list)

    p_show = sub.add_parser('show', help='Print a stored trajectory as JSON')
    _add_store_args(p_show)
    p_show.add_argument('trace_id', help='Trace ID to print')
    p_show.set_defaults(handler=_cmd_show)

    p_config = sub.add_parser('config', help='Manage local AgentDebugX config')
    _add_config_subcommands(p_config)

    p_rerun = sub.add_parser(
        'rerun',
        help='Second-stage entry point: rerun an agent from a diagnostic report',
    )
    _add_rerun_args(p_rerun)
    p_rerun.set_defaults(handler=_cmd_rerun)

    p_doctor = sub.add_parser('doctor', help='Report adapter and integration availability')
    p_doctor.set_defaults(handler=lambda _args: _cmd_doctor())

    p_serve = sub.add_parser('serve', help='Run the local FastAPI dashboard')
    _add_store_args(p_serve, required=True)
    p_serve.add_argument('--host', default='127.0.0.1')
    p_serve.add_argument('--port', type=int, default=7777)
    p_serve.set_defaults(handler=_cmd_serve)

    p_inspect = sub.add_parser('inspect', help='Run the local FastAPI dashboard')
    _add_store_args(p_inspect, required=True)
    p_inspect.add_argument('--host', default='127.0.0.1')
    p_inspect.add_argument('--port', type=int, default=7777)
    p_inspect.set_defaults(handler=_cmd_serve)

    p_act = sub.add_parser('act', help='Run advanced follow-up actions')
    act_sub = p_act.add_subparsers(dest='act_command', required=True)

    p_act_hub = act_sub.add_parser('hub', help='Error Hub - package, push, pull bundles')
    _add_hub_subcommands(p_act_hub)
    p_act_hub.set_defaults(handler=_cmd_hub)

    p_act_integrations = act_sub.add_parser('integrations', help='Generate host-runtime integrations')
    _add_integrations_subcommands(p_act_integrations)
    p_act_integrations.set_defaults(handler=_cmd_integrations)

    # ---- Hub subcommands ----
    p_hub = sub.add_parser('hub', help='Error Hub — package, push, pull bundles')
    _add_hub_subcommands(p_hub)
    p_hub.set_defaults(handler=_cmd_hub)

    # ---- Integrations subcommands ----
    p_int = sub.add_parser('integrations', help='Generate host-runtime integrations')
    _add_integrations_subcommands(p_int)
    p_int.set_defaults(handler=_cmd_integrations)

    args = parser.parse_args(argv)
    handler = getattr(args, 'handler', None)
    if handler is None:
        return 1
    return handler(args)


# ---------------- subcommands ----------------


def _cmd_diagnose(args: argparse.Namespace) -> int:
    try:
        trajectory = _load_target_trajectory(args, command_name='diagnose')
    except (OSError, ValueError) as exc:
        print(f'analyze failed: {exc}', file=sys.stderr)
        return 2

    if getattr(args, 'command', '') == 'diagnose':
        missing = [
            flag for flag, value in (
                ('--mode/--diagnoser', args.diagnose_mode),
                ('--attributor/--attribute', args.attributor_mode),
                ('--recovery/--recoverer', args.recovery_mode),
            )
            if value is None
        ]
        if missing:
            print(
                'diagnose requires explicit pipeline choices: '
                + ', '.join(missing),
                file=sys.stderr,
            )
            return 2

    diagnose_mode = _normalize_choice(
        args.diagnose_mode or 'heuristic',
        _DIAGNOSE_MODE_ALIASES,
        'diagnose mode',
    )
    attributor_mode = _normalize_choice(
        args.attributor_mode or 'none',
        _ATTRIBUTOR_ALIASES,
        'attributor',
    )
    if args.recovery_mode == 'auto':
        recovery_mode = 'deepdebug' if diagnose_mode == 'deep' else 'none'
    else:
        recovery_mode = _normalize_choice(
            args.recovery_mode or 'none',
            _RECOVERY_ALIASES,
            'recovery mode',
        )
    llm = None
    needs_llm = (
        diagnose_mode in {'judge', 'deep', 'gui-rca'}
        or attributor_mode in _LLM_ATTRIBUTORS
        or recovery_mode in _LLM_RECOVERIES
    )
    if needs_llm:
        llm = _build_llm(args, command_name='diagnose')
        if llm is None:
            return 4

    try:
        report = _run_diagnose_pipeline(
            args,
            trajectory,
            diagnose_mode=diagnose_mode,
            attributor_mode=attributor_mode,
            recovery_mode=recovery_mode,
            llm=llm,
        )
    except Exception as exc:
        print(f'diagnose failed: {exc}', file=sys.stderr)
        return 2

    if args.traceback:
        from agentdebug.traceback import format_traceback

        text = format_traceback(
            report, trajectory, use_color=not args.no_color and sys.stdout.isatty()
        )
        _emit(text, args.out)
        return 0
    rendered = model_to_json(report, indent=2)
    if args.suggest:
        from agentdebug.diagnose.context import DiagnoseContext
        from agentdebug.diagnose.recover import suggest_from_context

        proposals = suggest_from_context(
            ReflexionSuggestion(),
            DiagnoseContext.build(trajectory, report),
        )
        rendered = _augment_with_suggestions(rendered, report, proposals)
    _emit(rendered, args.out)
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    from agentdebug.adapters.importers import (
        ConversionError,
        convert_directory,
        convert_file,
    )

    try:
        if Path(args.input).is_dir():
            trajectory = convert_directory(
                args.input,
                format=args.format,
                trace_id=args.trace_id,
                task_id=args.task_id,
                goal=args.goal,
                framework=args.framework,
            )
        else:
            trajectory = convert_file(
                args.input,
                format=args.format,
                trace_id=args.trace_id,
                task_id=args.task_id,
                goal=args.goal,
                framework=args.framework,
            )
    except (ConversionError, OSError, ValueError) as exc:
        print(f'convert failed: {exc}', file=sys.stderr)
        return 2
    _emit(model_to_json(trajectory, indent=2), args.out)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    if store is None:
        print('No store configured. Use --store-sqlite or --store-jsonl.', file=sys.stderr)
        return 2
    for trace_id in store.list_traces():
        print(trace_id)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    if store is None:
        print('No store configured. Use --store-sqlite or --store-jsonl.', file=sys.stderr)
        return 2
    trajectory = store.load_trajectory(args.trace_id)
    if trajectory is None:
        print(f'Unknown trace_id: {args.trace_id}', file=sys.stderr)
        return 3
    print(model_to_json(trajectory, indent=2))
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    sub = args.config_command
    if sub == 'set-llm':
        config = _load_cli_config()
        config['llm'] = {
            'base_url': args.base_url,
            'api_key': args.api_key,
            'model': args.model,
        }
        if args.embedding_model:
            config['llm']['embedding_model'] = args.embedding_model
        path = _write_cli_config(config)
        print(f'wrote LLM config -> {path}')
        return 0

    if sub == 'show':
        config = _load_cli_config()
        rendered = config if args.show_secrets else _masked_config(config)
        print(json.dumps(rendered, indent=2))
        return 0

    if sub == 'clear-llm':
        config = _load_cli_config()
        config.pop('llm', None)
        path = _write_cli_config(config)
        print(f'cleared LLM config -> {path}')
        return 0

    if sub == 'doctor':
        llm = _build_llm(args, command_name='config doctor')
        if llm is None:
            return 4
        try:
            result = llm.complete(
                messages=[{'role': 'user', 'content': 'Say PONG'}],
                max_tokens=20,
                timeout=30.0,
            )
        except Exception as exc:
            print(f'LLM config check failed: {exc}', file=sys.stderr)
            return 5
        text = (getattr(result, 'text', '') or '').strip()
        print(json.dumps({'ok': True, 'model': llm.model, 'response': text}, indent=2))
        return 0

    if sub == 'set-runner':
        try:
            name = _validate_runner_name(args.name)
            runner = _runner_config_from_args(args)
        except ValueError as exc:
            print(f'runner config failed: {exc}', file=sys.stderr)
            return 2
        config = _load_cli_config()
        runners = config.setdefault('runners', {})
        if not isinstance(runners, dict):
            runners = {}
            config['runners'] = runners
        runners[name] = runner
        if args.default or not config.get('default_runner'):
            config['default_runner'] = name
        path = _write_cli_config(config)
        print(f'wrote runner {name!r} -> {path}')
        return 0

    if sub == 'list-runners':
        config = _load_cli_config()
        runners = config.get('runners') or {}
        print(
            json.dumps(
                {
                    'default_runner': config.get('default_runner'),
                    'runners': runners if isinstance(runners, dict) else {},
                },
                indent=2,
            )
        )
        return 0

    if sub == 'use-runner':
        config = _load_cli_config()
        runners = config.get('runners') or {}
        if not isinstance(runners, dict) or args.name not in runners:
            print(f'unknown runner: {args.name}', file=sys.stderr)
            return 2
        config['default_runner'] = args.name
        path = _write_cli_config(config)
        print(f'default runner is now {args.name!r} -> {path}')
        return 0

    if sub == 'remove-runner':
        config = _load_cli_config()
        runners = config.get('runners') or {}
        if not isinstance(runners, dict) or args.name not in runners:
            print(f'unknown runner: {args.name}', file=sys.stderr)
            return 2
        runners.pop(args.name)
        if config.get('default_runner') == args.name:
            config['default_runner'] = next(iter(runners), None)
        path = _write_cli_config(config)
        print(f'removed runner {args.name!r} -> {path}')
        return 0

    if sub == 'doctor-runner':
        executor = None
        try:
            name, runner = _configured_runner(args.name)
            executor = _http_executor_from_config(runner, source=None)
            capabilities = executor.capabilities()
        except Exception as exc:
            print(f'runner config check failed: {exc}', file=sys.stderr)
            return 5
        finally:
            if executor is not None:
                executor.close()
        print(json.dumps({'ok': True, 'runner': name, 'capabilities': capabilities}, indent=2))
        return 0

    return 1


def _cmd_rerun(args: argparse.Namespace) -> int:
    if args.actor_task_format and not args.plan_only:
        print('--actor-task-format requires --plan-only.', file=sys.stderr)
        return 2
    if args.plan_only and args.simulate:
        print('rerun accepts either --plan-only or --simulate, not both.', file=sys.stderr)
        return 2
    if args.plan_only and (args.runner_command or args.runner):
        print(
            'rerun accepts either --plan-only or a live runner, not both.',
            file=sys.stderr,
        )
        return 2
    report_path = Path(args.diagnostic_report)
    try:
        report_payload = json.loads(report_path.read_text(encoding='utf-8'))
        report = _parse_diagnostic_report(report_payload)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'rerun failed: could not read diagnostic report: {exc}', file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f'rerun failed: invalid diagnostic report: {exc}', file=sys.stderr)
        return 2

    trajectory_ref = args.trajectory
    trajectory = None
    if trajectory_ref:
        try:
            trajectory = _load_target_trajectory(args, command_name='rerun')
        except (OSError, ValueError) as exc:
            print(f'rerun failed: {exc}', file=sys.stderr)
            return 2

    checkpoint_policy = 'from_start'
    checkpoint_event_id = None
    if args.start_event is not None:
        if trajectory is None:
            print('rerun --start-event requires --trajectory.', file=sys.stderr)
            return 2
        if args.start_event < 1 or args.start_event > len(trajectory.events):
            print(
                f'rerun failed: --start-event must be between 1 and '
                f'{len(trajectory.events)} for this trajectory.',
                file=sys.stderr,
            )
            return 2
        checkpoint_policy = 'from_event'
        checkpoint_event_id = trajectory.events[args.start_event - 1].event_id

    if args.plan_only:
        plan = RerunWorkflow.suggest_only().plan(
            report,
            trajectory,
            checkpoint_policy=checkpoint_policy,
            checkpoint_event_id=checkpoint_event_id,
        )
        if args.actor_task_format:
            if trajectory is None:
                print(
                    'actor task export requires --trajectory with the original trace.',
                    file=sys.stderr,
                )
                return 2
            if not args.out:
                print(
                    'actor task export requires --out ending in .jsonl or .parquet.',
                    file=sys.stderr,
                )
                return 2
            expected_suffix = f'.{args.actor_task_format}'
            if Path(args.out).suffix.lower() != expected_suffix:
                print(
                    f'--actor-task-format {args.actor_task_format} requires '
                    f'an {expected_suffix} --out path.',
                    file=sys.stderr,
                )
                return 2
            from agentdebug.rerun import (
                build_actor_rerun_task,
                export_actor_rerun_tasks,
            )

            task = build_actor_rerun_task(plan.request, report, trajectory)
            try:
                export_actor_rerun_tasks(
                    [task],
                    args.out,
                    format=args.actor_task_format,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                print(f'actor task export failed: {exc}', file=sys.stderr)
                return 5
            return 0
        payload = {
            'stage': 'rerun',
            'status': plan.status,
            'executed': False,
            'plan': plan.to_dict(),
            'diagnostic_report': report_payload,
            'trajectory': trajectory_ref,
        }
        _emit(json.dumps(payload, indent=2), args.out)
        return 0

    if trajectory is None:
        print(
            'rerun execution requires --trajectory with the original trace; '
            'use --plan-only to prepare a request without execution.',
            file=sys.stderr,
        )
        return 2
    runner_command = str(
        args.runner_command or os.environ.get('AGENTDEBUG_RERUN_COMMAND') or ''
    ).strip()
    explicit_modes = sum(
        bool(value) for value in (runner_command, args.runner, args.simulate)
    )
    if explicit_modes > 1:
        print(
            'rerun accepts only one of --runner, --runner-command, or --simulate.',
            file=sys.stderr,
        )
        return 2
    if args.runner:
        try:
            _, runner = _configured_runner(args.runner)
            executor = _http_executor_from_config(runner, trajectory)
        except Exception as exc:
            print(f'rerun failed: {exc}', file=sys.stderr)
            return 4
        workflow = RerunWorkflow(executor)
    elif runner_command:
        from agentdebug.rerun import ProcessLiveExecutor

        runner_env = _live_runner_llm_env(args)
        executor = ProcessLiveExecutor(
            runner_command,
            trajectory,
            cwd=args.runner_cwd,
            timeout=args.runner_timeout,
            env=runner_env,
        )
        workflow = RerunWorkflow(executor)
    elif args.simulate:
        llm = _build_llm(args, command_name='rerun --simulate')
        if llm is None:
            return 4
        from agentdebug.rerun import LLMContinuationExecutor, RolloutContext

        executor = LLMContinuationExecutor(llm, RolloutContext(trajectory))
        workflow = RerunWorkflow(executor, allow_simulated=True)
    else:
        try:
            _, runner = _configured_runner(None)
            executor = _http_executor_from_config(runner, trajectory)
            workflow = RerunWorkflow(executor)
        except ValueError:
            print(
                'rerun requires a configured HTTP runner, --runner-command, or '
                '--simulate. Add one with `agentdebug config set-runner`.',
                file=sys.stderr,
            )
            return 4
    try:
        result = workflow.run(
            report,
            trajectory,
            execute=True,
            checkpoint_policy=checkpoint_policy,
            checkpoint_event_id=checkpoint_event_id,
        )
    except Exception as exc:
        print(f'rerun failed: {exc}', file=sys.stderr)
        return 5
    finally:
        close = getattr(locals().get('executor'), 'close', None)
        if callable(close):
            close()

    payload = result.to_dict()
    if result.execution is not None:
        payload['trajectory'] = json.loads(model_to_json(result.execution.trajectory))
        if args.store_sqlite or args.store_jsonl:
            store = _resolve_store(args)
            if store is not None:
                store.save_trajectory(result.execution.trajectory)
                payload['stored_trace_id'] = result.execution.trajectory.trace_id
    payload['diagnostic_report'] = report_payload
    _emit(json.dumps(payload, indent=2), args.out)
    return 0


def _live_runner_llm_env(args: argparse.Namespace) -> dict[str, str]:
    """Pass configured model access to the trusted runner without serializing it."""

    values = {
        'AGENTDEBUG_LIVE_BASE_URL': _resolve_llm_option(
            args,
            attr='base_url',
            env_name='AGENTDEBUG_LLM_BASE_URL',
            config_key='base_url',
        ),
        'AGENTDEBUG_LIVE_API_KEY': _resolve_llm_option(
            args,
            attr='api_key',
            env_name='AGENTDEBUG_LLM_API_KEY',
            config_key='api_key',
        ),
        'AGENTDEBUG_LIVE_MODEL': _resolve_llm_option(
            args,
            attr='model',
            env_name='AGENTDEBUG_LLM_MODEL',
            config_key='model',
        ),
    }
    return {key: str(value) for key, value in values.items() if value}


def _parse_diagnostic_report(payload: dict[str, Any]) -> DiagnosticReport:
    if (
        'events' in payload
        and 'findings' not in payload
        and 'summary' not in payload
        and 'report_id' not in payload
    ):
        raise ValueError(
            'expected a DiagnosticReport JSON, got a trajectory-like payload'
        )
    validator = getattr(DiagnosticReport, 'model_validate', None)
    try:
        if callable(validator):
            return validator(payload)
        return DiagnosticReport.parse_obj(payload)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def _cmd_serve(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    if store is None:
        print(
            'serve requires --store-sqlite or --store-jsonl.', file=sys.stderr
        )
        return 2
    try:
        from agentdebug.ui import serve

        serve(store, host=args.host, port=args.port)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    return 0


def _cmd_hub(args: argparse.Namespace) -> int:
    from agentdebug.analyzers import HeuristicAnalyzer
    from agentdebug.hub import (
        Bundle,
        backend_from_spec,
    )
    from agentdebug.hub.scrub import SCRUBBER_VERSION, scrub_trajectory

    sub = args.hub_command
    if sub == 'push':
        store = _resolve_store(args)
        if store is None:
            print('hub push requires --store-sqlite or --store-jsonl', file=sys.stderr)
            return 2
        trajectory = store.load_trajectory(args.trace_id)
        if trajectory is None:
            print(f'unknown trace_id: {args.trace_id}', file=sys.stderr)
            return 3
        scrubbed_flag = not args.no_scrub
        scrubber_version = None
        if scrubbed_flag:
            report = scrub_trajectory(trajectory)
            scrubber_version = SCRUBBER_VERSION
            print(
                f'scrubbed: visited={report.fields_visited} '
                f'replacements={dict(report.replacements)}',
                file=sys.stderr,
            )
        # Re-run rule analyzer post-scrub for a deterministic report.
        diag = HeuristicAnalyzer().analyze(trajectory)
        # build_manifest is in agentdebug.hub.bundle
        from agentdebug.hub.bundle import build_manifest as _bm

        manifest = _bm(
            trajectory,
            report=diag,
            license=args.license,
            contributor=args.contributor,
            contributor_org=args.contributor_org,
            scrubbed=scrubbed_flag,
            scrubber_version=scrubber_version,
        )
        bundle = Bundle(manifest=manifest, trajectory=trajectory, report=diag)
        backend = backend_from_spec(args.to)
        try:
            ref = backend.push(bundle, message=args.message)
        except Exception as exc:
            print(f'hub push failed: {exc}', file=sys.stderr)
            return 4
        print(f'pushed bundle {manifest.bundle_id} -> {ref}')
        return 0
    if sub == 'pull':
        from pathlib import Path

        backend = backend_from_spec(args.spec)
        into = Path(args.into)
        into.mkdir(parents=True, exist_ok=True)
        try:
            path = backend.pull(args.bundle, into=into)
        except Exception as exc:
            print(f'hub pull failed: {exc}', file=sys.stderr)
            return 4
        print(f'pulled {args.bundle} -> {path}')
        return 0
    if sub == 'list':
        backend = backend_from_spec(args.spec)
        try:
            ids = backend.list_bundles(limit=args.limit)
        except Exception as exc:
            print(f'hub list failed: {exc}', file=sys.stderr)
            return 4
        for bid in ids:
            print(bid)
        return 0
    return 1


def _cmd_integrations(args: argparse.Namespace) -> int:
    from agentdebug.integrations import (
        OpenHandsMicroagentContract,
        build_debug_skill_bundle,
        build_skill_bundle,
        write_debug_skill_bundle,
        write_skill_bundle,
    )

    if args.int_command == 'skill':
        if args.platform == 'claude':
            bundle = build_skill_bundle(name=args.name)
            path = write_skill_bundle(bundle, target_dir=Path(args.target))
        else:
            bundle = build_debug_skill_bundle(platform=args.platform, name=args.name)
            path = write_debug_skill_bundle(bundle, target_dir=Path(args.target))
        print(f'wrote {args.platform} skill -> {path}')
        return 0
    if args.int_command == 'openhands-microagent':
        contract = OpenHandsMicroagentContract(name=args.name)
        path = contract.write(Path(args.target))
        print(f'wrote OpenHands microagent -> {path}')
        return 0
    return 1


def _cmd_doctor() -> int:
    from agentdebug.plugins import list_plugins

    statuses = []
    try:
        from agentdebug.adapters.langgraph import LangGraphAdapter

        statuses.append(LangGraphAdapter().instrument(_dummy_debugger()))
    except Exception as exc:  # pragma: no cover - defensive
        statuses.append(_status('langgraph', False, str(exc)))
    try:
        from agentdebug.adapters.crewai import CrewAIAdapter

        statuses.append(CrewAIAdapter().instrument(_dummy_debugger()))
    except Exception as exc:  # pragma: no cover - defensive
        statuses.append(_status('crewai', False, str(exc)))
    try:
        from agentdebug.adapters.openai_agents import OpenAIAgentsAdapter

        statuses.append(OpenAIAgentsAdapter().instrument(_dummy_debugger()))
    except Exception as exc:  # pragma: no cover - defensive
        statuses.append(_status('openai-agents', False, str(exc)))
    try:
        from agentdebug.adapters.otel import OTelExportAdapter

        statuses.append(OTelExportAdapter().instrument(_dummy_debugger()))
    except Exception as exc:  # pragma: no cover - defensive
        statuses.append(_status('otel', False, str(exc)))
    from agentdebug.adapters.raw import RawLoopAdapter

    statuses.append(RawLoopAdapter().instrument(_dummy_debugger()))
    statuses.append(_gui_integration_status())
    for s in statuses:
        flag = '✓' if s.implemented else '✗'
        print(f'  {flag}  {s.framework:<10} {s.notes}')
    plugins = list_plugins()
    if plugins:
        print('\nplugins:')
        for plugin in plugins:
            caps = ','.join(plugin.capabilities) if plugin.capabilities else '-'
            print(
                f'  - {plugin.plugin_type:<8} {plugin.display_name} '
                f'[{plugin.plugin_id}] caps={caps}'
            )
    return 0


# ---------------- helpers ----------------


def _add_store_args(p: argparse.ArgumentParser, *, required: bool = False) -> None:
    group = p.add_mutually_exclusive_group(required=required)
    group.add_argument('--store-sqlite', help='Path to a SQLite trace store')
    group.add_argument('--store-jsonl', help='Path to a JSONL trace store')


def _resolve_store(args: argparse.Namespace) -> Optional[TraceStore]:
    if getattr(args, 'store_sqlite', None):
        return SQLiteTraceStore(args.store_sqlite)
    if getattr(args, 'store_jsonl', None):
        return JsonlTraceStore(args.store_jsonl)
    return None


def _config_path() -> Path:
    configured = os.environ.get('AGENTDEBUG_CONFIG')
    if configured:
        return Path(configured).expanduser()
    return Path.home() / '.agentdebug' / 'config.json'


def _load_cli_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cli_config(config: dict[str, Any]) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _configured_llm() -> dict[str, Any]:
    llm = _load_cli_config().get('llm') or {}
    return llm if isinstance(llm, dict) else {}


def _validate_runner_name(value: object) -> str:
    name = str(value or '').strip()
    if not name or not all(character.isalnum() or character in '-_' for character in name):
        raise ValueError('runner name must contain only letters, numbers, - or _')
    return name


def _runner_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from agentdebug.rerun import normalize_http_runner_url

    url = normalize_http_runner_url(str(args.url or ''))
    parsed = urlparse(url)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError('runner URL must be an absolute http:// or https:// URL')
    if (
        args.timeout <= 0
        or args.poll_interval < 0
        or args.max_retries < 0
        or args.retry_delay < 0
    ):
        raise ValueError('runner timeout/retry values are invalid')
    token_env = str(args.token_env or '').strip() or None
    if token_env and not token_env.replace('_', '').isalnum():
        raise ValueError('token environment variable name is invalid')
    return {
        'url': url,
        'token_env': token_env,
        'timeout': float(args.timeout),
        'poll_interval': float(args.poll_interval),
        'max_retries': int(args.max_retries),
        'retry_delay': float(args.retry_delay),
        'verify_tls': not bool(args.insecure),
    }


def _configured_runner(name: Optional[str]) -> tuple[str, dict[str, Any]]:
    config = _load_cli_config()
    runner_name = _validate_runner_name(name or config.get('default_runner'))
    runners = config.get('runners') or {}
    if not isinstance(runners, dict) or runner_name not in runners:
        raise ValueError(f'unknown runner: {runner_name}')
    runner = runners[runner_name]
    if not isinstance(runner, dict):
        raise ValueError(f'invalid runner configuration: {runner_name}')
    return runner_name, runner


def _http_executor_from_config(
    config: dict[str, Any],
    source: Optional[AgentTrajectory],
) -> Any:
    from agentdebug.rerun import HttpLiveExecutor, normalize_http_runner_url

    url = normalize_http_runner_url(str(config.get('url') or ''))
    parsed = urlparse(url)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError('configured runner URL is invalid')
    token_env = str(config.get('token_env') or '').strip()
    token = os.environ.get(token_env) if token_env else None
    if token_env and not token:
        raise ValueError(f'runner token environment variable is not set: {token_env}')
    return HttpLiveExecutor(
        url,
        source or AgentTrajectory(trace_id='runner_doctor_probe'),
        token=token,
        timeout=float(config.get('timeout') or 1800),
        poll_interval=float(config.get('poll_interval') or 1),
        max_retries=int(config.get('max_retries', 3)),
        retry_delay=float(config.get('retry_delay', 0.5)),
        verify_tls=bool(config.get('verify_tls', True)),
    )


def _mask_secret(value: object) -> object:
    text = '' if value is None else str(value)
    if len(text) <= 8:
        return '***' if text else ''
    return f'{text[:4]}...{text[-4:]}'


def _masked_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = json.loads(json.dumps(config))
    llm = masked.get('llm')
    if isinstance(llm, dict) and 'api_key' in llm:
        llm['api_key'] = _mask_secret(llm.get('api_key'))
    return masked


def _resolve_llm_option(
    args: argparse.Namespace,
    *,
    attr: str,
    env_name: str,
    config_key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    cli_value = getattr(args, attr, None)
    if cli_value:
        return str(cli_value)
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    config_value = _configured_llm().get(config_key)
    if config_value:
        return str(config_value)
    return default


def _normalize_choice(value: str, aliases: dict[str, str], label: str) -> str:
    try:
        return aliases[value]
    except KeyError as exc:
        valid = ', '.join(sorted(aliases))
        raise ValueError(f'unknown {label} {value!r}; expected one of: {valid}') from exc


def _load_target_trajectory(
    args: argparse.Namespace, *, command_name: str
) -> AgentTrajectory:
    target = getattr(args, 'target', None) or getattr(args, 'trajectory', None)
    if not target:
        raise ValueError(f'{command_name} requires a trajectory path or trace_id')

    target_path = Path(target)
    if target_path.exists():
        return _load_trajectory_file(target_path)

    store = _resolve_store(args)
    if store is None:
        raise ValueError(
            f'could not find {target!r} on disk and no store configured'
        )
    loaded = store.load_trajectory(target)
    if loaded is None:
        raise ValueError(f'unknown trace_id: {target}')
    return loaded


def _build_llm(args: argparse.Namespace, *, command_name: str) -> Optional[Any]:
    from agentdebug.llm import OpenAICompatClient

    base_url = _resolve_llm_option(
        args,
        attr='base_url',
        env_name='AGENTDEBUG_LLM_BASE_URL',
        config_key='base_url',
    )
    api_key = _resolve_llm_option(
        args,
        attr='api_key',
        env_name='AGENTDEBUG_LLM_API_KEY',
        config_key='api_key',
    )
    model = _resolve_llm_option(
        args,
        attr='model',
        env_name='AGENTDEBUG_LLM_MODEL',
        config_key='model',
        default='gemini-3-flash',
    )
    embedding_model = _resolve_llm_option(
        args,
        attr='embedding_model',
        env_name='AGENTDEBUG_LLM_EMBEDDING_MODEL',
        config_key='embedding_model',
        default='text-embedding-3-small',
    )
    if not base_url or not api_key:
        print(
            f'{command_name} requires --base-url and --api-key (or '
            'AGENTDEBUG_LLM_BASE_URL / AGENTDEBUG_LLM_API_KEY, or '
            '`agentdebug config set-llm`).',
            file=sys.stderr,
        )
        return None
    return OpenAICompatClient(
        base_url=base_url,
        api_key=api_key,
        model=model or 'gemini-3-flash',
        embedding_model=embedding_model or 'text-embedding-3-small',
        default_max_tokens=8192,
        timeout=180.0,
    )


def _run_diagnose_mode(
    args: argparse.Namespace,
    trajectory: AgentTrajectory,
    diagnose_mode: str,
    llm: Optional[Any],
) -> DiagnosticReport:
    if diagnose_mode == 'heuristic':
        return HeuristicAnalyzer(rule_packs=args.rule_pack or 'auto').analyze(
            trajectory
        )

    if diagnose_mode == 'judge':
        if llm is None:
            raise ValueError('judge mode requires an LLM client')
        from agentdebug.judges import LLMJudgeAnalyzer

        return LLMJudgeAnalyzer(llm=llm).analyze(trajectory)

    if diagnose_mode == 'deep':
        if llm is None:
            raise ValueError('deep mode requires an LLM client')
        from agentdebug.deep import DeepDebugAnalyzer
        from agentdebug.deep_memory import SQLiteDeepMemoryStore

        detect_report = HeuristicAnalyzer(
            rule_packs=args.rule_pack or 'auto'
        ).analyze(trajectory)
        memory_store = (
            SQLiteDeepMemoryStore(embedder=llm)
            if getattr(args, 'embedding_model', None)
            else SQLiteDeepMemoryStore()
        )
        report = DeepDebugAnalyzer(
            llm=llm,
            memory_store=memory_store,
            prior_findings=detect_report.findings,
        ).analyze(trajectory).report
        report.metadata['upstream_detect'] = {
            'analyzer': detect_report.metadata.get('analyzer'),
            'summary': detect_report.summary,
            'finding_count': len(detect_report.findings),
            'findings': [
                {
                    'finding_id': finding.finding_id,
                    'failure_mode_id': finding.failure_mode.mode_id,
                    'failure_mode_name': finding.failure_mode.name,
                    'event_id': finding.event_id,
                    'agent_name': finding.agent_name,
                    'step_index': finding.step_index,
                    'evidence': list(finding.evidence),
                    'suggestion': finding.suggestion,
                }
                for finding in detect_report.findings
            ],
        }
        return report

    if diagnose_mode == 'gui-rca':
        # OSWorld GUI root-cause analysis. Hard prerequisite: the configured
        # AGENTDEBUG_LLM_* backend must support tool-calling AND vision — the
        # vendored ReAct loop drives tool calls and inspects screenshots.
        if llm is None:
            raise ValueError('gui-rca mode requires an LLM client')
        from agentdebug.runtime.llm_channel import CoreLLMChannel
        from agentdebug.diagnose.gui_rca import GuiRcaAnalyzer

        channel = CoreLLMChannel(llm)
        return GuiRcaAnalyzer(channel=channel, model=llm.model).analyze(trajectory)

    raise ValueError(f'unknown diagnose mode: {diagnose_mode}')


def _run_diagnose_pipeline(
    args: argparse.Namespace,
    trajectory: AgentTrajectory,
    *,
    diagnose_mode: str,
    attributor_mode: str,
    recovery_mode: str,
    llm: Optional[Any],
) -> DiagnosticReport:
    """Run the shared Detect -> Attribute -> Recover CLI pipeline."""

    from agentdebug.diagnose.context import DiagnoseContext

    report = _run_diagnose_mode(args, trajectory, diagnose_mode, llm)
    attribution = None
    if attributor_mode != 'none' and diagnose_mode != 'deep':
        attribution = _run_attributor(attributor_mode, trajectory, report, llm)
        report.attribution = _attribution_to_payload(attribution)
    context = DiagnoseContext.build(trajectory, report, attribution)
    if recovery_mode != 'none':
        proposals = _run_recovery(recovery_mode, context, llm)
        report.suggestions = [proposal.suggestion_text for proposal in proposals]
        report.recovery = _recovery_to_payload(recovery_mode, proposals)
    return report


def _run_attributor(
    attributor_mode: str,
    trajectory: AgentTrajectory,
    report: DiagnosticReport,
    llm: Optional[Any],
) -> Any:
    if attributor_mode == 'heuristic':
        return HeuristicAttributor().attribute(trajectory, report.findings)

    if llm is None:
        raise ValueError(f'{attributor_mode} attribution requires an LLM client')

    if attributor_mode == 'all_at_once':
        return AllAtOnceAttributor(llm=llm).attribute(trajectory, report.findings)
    if attributor_mode == 'step_by_step':
        return StepByStepAttributor(llm=llm).attribute(trajectory, report.findings)
    if attributor_mode == 'binary_search':
        return BinarySearchAttributor(llm=llm).attribute(trajectory, report.findings)
    if attributor_mode == 'counterfactual':
        return CounterfactualAttributor(llm=llm).attribute(
            trajectory, report.findings
        )

    raise ValueError(f'unknown attributor: {attributor_mode}')


def _run_recovery(
    recovery_mode: str,
    context: Any,
    llm: Optional[Any],
) -> list[FixProposal]:
    from agentdebug.diagnose.recover import suggest_from_context

    if recovery_mode == 'deepdebug':
        return suggest_from_context(DeepDebugRecovery(), context)
    if recovery_mode == 'reflexion':
        return suggest_from_context(ReflexionSuggestion(), context)
    if recovery_mode == 'critic':
        return suggest_from_context(CriticRecoverer(), context)
    if recovery_mode == 'self_refine':
        if llm is None:
            raise ValueError('self_refine recovery requires an LLM client')
        return suggest_from_context(SelfRefineLoop(llm=llm), context)
    if recovery_mode == 'auto_manual':
        return suggest_from_context(AutoManualRules(llm=llm), context)
    if recovery_mode == 'saga_rollback':
        # No compensations are registered from CLI yet; this safely returns
        # an empty plan unless a future runner wires project-specific tools.
        return suggest_from_context(SagaRollback(Compensator()), context)

    raise ValueError(f'unknown recovery mode: {recovery_mode}')


def _attribution_to_payload(blame_result: Any) -> dict[str, Any]:
    payload = asdict(blame_result)
    hypotheses = payload.get('hypotheses') or []
    payload['primary'] = hypotheses[0] if hypotheses else None
    return payload


def _recovery_to_payload(
    recovery_mode: str, proposals: list[FixProposal]
) -> dict[str, Any]:
    payload = {
        'method': recovery_mode,
        'proposal_count': len(proposals),
        'proposals': [asdict(proposal) for proposal in proposals],
    }
    payload['primary'] = payload['proposals'][0] if proposals else None
    return payload


def _load_trajectory_file(path: Path) -> AgentTrajectory:
    """Load a trajectory file, auto-normalizing common external formats.

    This keeps ``analyze``/``judge``/``deep`` convenient for users who point
    the CLI at exported benchmark/framework traces instead of already-converted
    ``AgentTrajectory`` JSON.
    """

    from agentdebug.adapters.importers import (
        ConversionError,
        convert_file,
        convert_payload,
        detect_payload_format,
    )

    if path.suffix.lower() == '.jsonl':
        trajectory = convert_file(path, format='auto')
        trajectory.metadata.setdefault('loaded_via', 'cli_auto_convert')
        return trajectory

    raw_text = path.read_text(encoding='utf-8')
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'could not parse {path} as JSON: {exc}') from exc

    try:
        fmt = detect_payload_format(payload)
    except ConversionError as exc:
        raise ValueError(f'could not detect a supported trajectory format for {path}: {exc}') from exc

    if fmt == 'agenttrajectory':
        return trajectory_from_json(raw_text)

    trajectory = convert_payload(payload, format=fmt)
    # Preserve that the CLI performed an in-memory normalization step
    # without touching the user's original file on disk.
    trajectory.metadata.setdefault('loaded_via', 'cli_auto_convert')
    return trajectory


def _emit(rendered: str, out_path: Optional[str]) -> None:
    if out_path is None:
        print(rendered)
        return
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered + '\n', encoding='utf-8')


def _augment_with_suggestions(
    rendered: str, report: DiagnosticReport, proposals: list[FixProposal]
) -> str:
    proposals_block = '\n'.join(
        f'-- proposal {p.proposal_id} ({p.recoverer_id}) --\n{p.suggestion_text}'
        for p in proposals
    )
    return rendered + (
        '\n\n# === Reflexion suggestions ===\n' + proposals_block
        if proposals_block
        else ''
    )


def _augment_with_blame(rendered: str, blame_result: object) -> str:
    return rendered + '\n\n# === Attribution ===\n' + repr(blame_result)


def _status(framework: str, implemented: bool, notes: str) -> object:
    from agentdebug.adapters.base import AdapterStatus

    return AdapterStatus(framework=framework, implemented=implemented, notes=notes)


def _gui_integration_status() -> object:
    """Defensively probe whether the GUI / OSWorld (CUA) integration is available.

    Never hard-imports a GUI dependency at module top level: the probe imports
    are confined to this guarded body so `import agentdebug.cli` stays free of
    the optional `gui` extra.
    """
    try:
        import anthropic  # noqa: F401
        import chromadb  # noqa: F401
        import streamlit  # noqa: F401

        return _status('gui', True, 'GUI/OSWorld (CUA) integration deps available')
    except Exception:  # pragma: no cover - defensive
        return _status(
            'gui', False, 'not installed — run `pip install agentdebugx[gui]`'
        )


def _dummy_debugger() -> object:
    from agentdebug.recorder import AgentDebug
    from agentdebug.storage import JsonlTraceStore

    return AgentDebug(store=JsonlTraceStore('.agentdebug/_doctor.jsonl'))


if __name__ == '__main__':
    raise SystemExit(main())
