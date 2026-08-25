"""Thin CLI entry point.

This module owns parser assembly and command dispatch. Subcommand workflows live
under ``agentdebug.cli.commands``; the historical implementation remains in
``agentdebug.cli.legacy`` as a compatibility layer while the workflows are
extracted incrementally.
"""

from __future__ import annotations

import argparse
import sys
from types import ModuleType
from collections.abc import Sequence
from typing import Optional

from agentdebug.cli import legacy
from agentdebug.cli.commands import (
    batch,
    diagnose,
    doctor,
    hub,
    ingest,
    integrations,
    rerun,
    run,
    runner,
    serve,
    store,
    ui,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='agentdebug')
    sub = parser.add_subparsers(dest='command', required=True)

    p_analyze = sub.add_parser(
        'analyze',
        help='Compatibility alias: heuristic-only diagnosis',
    )
    legacy._add_diagnose_args(
        p_analyze,
        trajectory_help='Path to an AgentTrajectory JSON file',
        require_pipeline=False,
    )
    p_analyze.set_defaults(handler=diagnose.run)

    p_diagnose = sub.add_parser(
        'diagnose',
        help='Run diagnosis, attribution, and recovery planning',
    )
    legacy._add_diagnose_args(
        p_diagnose,
        trajectory_help='Path to a trajectory or external trace file',
        require_pipeline=False,
    )
    p_diagnose.set_defaults(handler=diagnose.run)

    p_convert = sub.add_parser(
        'convert',
        help='Compatibility alias for ingest',
    )
    legacy._add_ingest_args(p_convert)
    p_convert.set_defaults(handler=ingest.run)

    p_ingest = sub.add_parser(
        'ingest',
        help='Normalize an offline trace export into AgentTrajectory JSON',
    )
    legacy._add_ingest_args(p_ingest)
    p_ingest.set_defaults(handler=ingest.run)

    p_run = sub.add_parser('run', help='Run one durable, unified debug workflow')
    p_run.add_argument('input', help='Trajectory/export path or stored trace ID')
    p_run.add_argument('--profile', choices=('quick', 'standard', 'deep', 'gui'), default='standard')
    p_run.add_argument('--format', dest='format_override', default=None)
    p_run.add_argument('--diagnoser')
    p_run.add_argument('--attributor')
    p_run.add_argument('--recovery')
    store_group = p_run.add_mutually_exclusive_group()
    store_group.add_argument('--store-sqlite')
    store_group.add_argument('--store-jsonl')
    p_run.add_argument('--run-root', default='.agentdebug')
    p_run.add_argument('--plan', action='store_true')
    p_run.add_argument('--ui', action='store_true')
    p_run.add_argument('--json', action='store_true')
    p_run.set_defaults(handler=run.run)

    p_ui = sub.add_parser('ui', help='Manage the local inspection UI')
    ui_sub = p_ui.add_subparsers(dest='ui_command', required=True)
    p_ui_ensure = ui_sub.add_parser('ensure')
    p_ui_ensure.add_argument('--run-id', required=True)
    p_ui_ensure.add_argument('--run-root', default='.agentdebug')
    p_ui_ensure.add_argument('--open-browser', action='store_true')
    p_ui_ensure.add_argument('--json', action='store_true')
    p_ui_ensure.set_defaults(handler=ui.run)
    p_ui_status = ui_sub.add_parser('status')
    p_ui_status.add_argument('--run-root', default='.agentdebug')
    p_ui_status.add_argument('--json', action='store_true')
    p_ui_status.set_defaults(handler=ui.run)

    p_batch = sub.add_parser(
        'batch',
        help='Process directories or independent JSONL records',
    )
    batch_sub = p_batch.add_subparsers(dest='batch_command', required=True)

    p_batch_ingest = batch_sub.add_parser(
        'ingest',
        help='Normalize every JSON file or JSONL record',
    )
    p_batch_ingest.add_argument('input')
    p_batch_ingest.add_argument('--out-dir', required=True)
    p_batch_ingest.add_argument(
        '--format', default='auto', choices=legacy._TRACE_FORMAT_CHOICES
    )
    p_batch_ingest.add_argument('--goal')
    p_batch_ingest.add_argument('--framework')
    p_batch_ingest.set_defaults(handler=batch.run)

    p_batch_diagnose = batch_sub.add_parser(
        'diagnose',
        help='Normalize and diagnose every JSON file or JSONL record',
    )
    legacy._add_batch_diagnose_args(p_batch_diagnose)
    p_batch_diagnose.add_argument('--out-dir', required=True)
    p_batch_diagnose.add_argument(
        '--format', default='auto', choices=legacy._TRACE_FORMAT_CHOICES
    )
    p_batch_diagnose.add_argument('--goal')
    p_batch_diagnose.add_argument('--framework')
    p_batch_diagnose.set_defaults(handler=batch.run)

    p_list = sub.add_parser('list', help='List trace IDs in a store')
    legacy._add_store_args(p_list)
    p_list.set_defaults(handler=store.list_traces)

    p_show = sub.add_parser('show', help='Print a stored trajectory as JSON')
    legacy._add_store_args(p_show)
    p_show.add_argument('trace_id', help='Trace ID to print')
    p_show.set_defaults(handler=store.show_trace)

    p_config = sub.add_parser('config', help='Manage local AgentDebugX config')
    legacy._add_config_subcommands(p_config)

    p_rerun = sub.add_parser(
        'rerun',
        help='Second-stage entry point: rerun an agent from a diagnostic report',
    )
    legacy._add_rerun_args(p_rerun)
    p_rerun.set_defaults(handler=rerun.run)

    p_runner = sub.add_parser(
        'runner', help='Serve an application-owned agent over the Rerun HTTP protocol'
    )
    runner_sub = p_runner.add_subparsers(dest='runner_command', required=True)
    p_runner_serve = runner_sub.add_parser('serve', help='Start a persistent runner')
    p_runner_serve.add_argument('entrypoint', help='Python callback as module:function')
    p_runner_serve.add_argument('--name', required=True)
    p_runner_serve.add_argument('--framework', required=True)
    p_runner_serve.add_argument('--host', default='127.0.0.1')
    p_runner_serve.add_argument('--port', type=int, default=8765)
    p_runner_serve.add_argument('--token-env')
    p_runner_serve.add_argument('--max-concurrency', type=int, default=1)
    p_runner_serve.add_argument(
        '--checkpoint-policy',
        action='append',
        choices=['from_start', 'from_root_cause', 'from_event'],
    )
    p_runner_serve.add_argument('--environment-restore', action='store_true')
    p_runner_serve.set_defaults(handler=runner.run)

    p_doctor = sub.add_parser('doctor', help='Report adapter and integration availability')
    p_doctor.set_defaults(handler=lambda _args: doctor.run())

    p_serve = sub.add_parser('serve', help='Run the local FastAPI dashboard')
    legacy._add_store_args(p_serve, required=True)
    p_serve.add_argument('--host', default='127.0.0.1')
    p_serve.add_argument('--port', type=int, default=7777)
    p_serve.set_defaults(handler=serve.run)

    p_inspect = sub.add_parser('inspect', help='Run the local FastAPI dashboard')
    legacy._add_store_args(p_inspect, required=True)
    p_inspect.add_argument('--host', default='127.0.0.1')
    p_inspect.add_argument('--port', type=int, default=7777)
    p_inspect.set_defaults(handler=serve.run)

    p_act = sub.add_parser('act', help='Run advanced follow-up actions')
    act_sub = p_act.add_subparsers(dest='act_command', required=True)

    p_act_hub = act_sub.add_parser('hub', help='Error Hub - package, push, pull bundles')
    legacy._add_hub_subcommands(p_act_hub)
    p_act_hub.set_defaults(handler=hub.run)

    p_act_integrations = act_sub.add_parser(
        'integrations',
        help='Generate host-runtime integrations',
    )
    legacy._add_integrations_subcommands(p_act_integrations)
    p_act_integrations.set_defaults(handler=integrations.run)

    p_hub = sub.add_parser('hub', help='Error Hub — package, push, pull bundles')
    legacy._add_hub_subcommands(p_hub)
    p_hub.set_defaults(handler=hub.run)

    p_int = sub.add_parser('integrations', help='Generate host-runtime integrations')
    legacy._add_integrations_subcommands(p_int)
    p_int.set_defaults(handler=integrations.run)

    args = parser.parse_args(argv)
    handler = getattr(args, 'handler', None)
    if handler is None:
        return 1
    return handler(args)


# Importing a submodule normally replaces ``agentdebug.cli.main`` on the parent
# package with the module object. Preserve the long-standing callable package
# API while keeping package initialization lazy enough for service reuse.
_parent_package = sys.modules.get('agentdebug.cli')
if _parent_package is not None:
    _parent_package.main = main  # type: ignore[attr-defined]


class _CallableMainModule(ModuleType):
    """Keep ``from agentdebug.cli import main`` callable after submodule imports."""

    def __call__(self, argv: Optional[Sequence[str]] = None) -> int:
        return main(argv)


sys.modules[__name__].__class__ = _CallableMainModule
