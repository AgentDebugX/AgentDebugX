"""Drive Harbor over Terminal-Bench 2.1 and collect the per-trial output.

Two subcommands:

  run      launch one method (a harbor run over a task list) and print its job dir
  collect  walk a job dir and emit one JSONL record per trial

The point of ``collect`` is to pin down the input/output contract once, so the
rest of the experiment reads a stable record shape instead of harbor internals.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if __package__:
    from .agentdebug_artifact import AgentDebugInstallerConfig
    from .claude_artifact import ClaudeInstallerConfig
else:
    from agentdebug_artifact import AgentDebugInstallerConfig  # type: ignore[no-redef]
    from claude_artifact import ClaudeInstallerConfig  # type: ignore[no-redef]

DATASET = 'terminal-bench/terminal-bench-2-1'
ENVIRONMENT_BACKENDS = ('docker', 'singularity')
# Set AGENTDEBUG_BIN to pin a specific env's CLI (e.g. the agent-debugx conda env).
AGENTDEBUG_BIN = os.environ.get('AGENTDEBUG_BIN', 'agentdebug')
TASK_PREFIX = 'terminal-bench/'
JOB_DIR_RE = re.compile(r'Results written to\s+(\S+)/result\.json')
PINNED_CLAUDE_AGENT = (
    'examples.terminal_bench_eval.pinned_claude:PinnedClaudeCode'
)
AGENTDEBUG_CLAUDE_AGENT = (
    'examples.terminal_bench_eval.agentdebug_claude:AgentDebugClaudeCode'
)


def _resolved_installer_kwargs(config_path: Path) -> list[str]:
    resolved_path = config_path.expanduser().resolve()
    config = ClaudeInstallerConfig.load(resolved_path)
    return [
        f'claude_artifact_path={config.artifact.path}',
        f'claude_artifact_sha256={config.artifact.sha256}',
        f'claude_artifact_version={config.artifact.version}',
        f'claude_install_path={config.install_path}',
        f'claude_installer_config_source={resolved_path}',
    ]


def _resolved_agentdebug_installer_kwargs(config_path: Path) -> list[str]:
    resolved_path = config_path.expanduser().resolve()
    config = AgentDebugInstallerConfig.load(resolved_path)
    return [
        f'agentdebug_wheel_path={config.artifact.path}',
        f'agentdebug_wheel_sha256={config.artifact.sha256}',
        f'agentdebug_version={config.artifact.version}',
        f'agentdebug_installer_config_source={resolved_path}',
    ]


def _read_tasks(tasks_file: Path) -> list[str]:
    """One task name per line; blank lines and ``#`` comments ignored."""
    names = []
    for line in tasks_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        names.append(line if line.startswith(TASK_PREFIX) else TASK_PREFIX + line)
    return names


def _build_mounts_json(mounts: list[str]) -> str:
    """Convert repeatable ``SRC:DST`` values into harbor's ``--mounts`` JSON array.

    Harbor's ``--mounts`` takes a JSON array of ``ServiceVolumeConfig``
    objects, not Docker Compose short-syntax strings (verified against
    harbor 0.21.0's ``cli/jobs.py`` and ``models/trial/config.py`` — the
    singularity backend only honors ``type == "bind"`` entries). Every mount
    built here is read-only, matching the immutable diagnostic-input contract
    in docs/architecture.md.
    """
    volumes = []
    for mount in mounts:
        source, sep, target = mount.partition(':')
        if not sep or not source or not target:
            raise ValueError(f"--mount must be 'SRC:DST', got {mount!r}")
        volumes.append({'type': 'bind', 'source': source, 'target': target, 'read_only': True})
    return json.dumps(volumes)


def _resolve_agent_env(names: list[str]) -> list[str]:
    """Resolve each named host env var to a harbor ``NAME=value`` agent-env entry.

    Only the named variables are read from the host environment (docs/
    architecture.md, "Pass only the required AGENTDEBUG_LLM_* values through
    Harbor agent env") — this never forwards the whole environment. Missing
    a named variable fails the run rather than silently starting a method
    without the credentials its method needs.
    """
    resolved = []
    for name in names:
        value = os.environ.get(name)
        if value is None:
            raise ValueError(f'--agent-env {name} is not set in the host environment')
        resolved.append(f'{name}={value}')
    return resolved


def _loggable_command(cmd: list[str]) -> str:
    """Render ``cmd`` for the '+ ...' log line with agent-env values masked."""
    parts = []
    mask_next = False
    for part in cmd:
        if mask_next:
            name = part.split('=', 1)[0]
            parts.append(f'{name}=***')
            mask_next = False
        else:
            parts.append(part)
        if part == '--agent-env':
            mask_next = True
    return ' '.join(parts)


def _job_dir_from_output(output: str) -> str | None:
    """Extract Harbor's job dir even when Rich wraps a long path mid-token."""
    unwrapped = re.sub(r'(?<=\S)\r?\n(?=\S)', '', output)
    match = JOB_DIR_RE.search(unwrapped)
    return match.group(1) if match else None


def _build_run_command(args: argparse.Namespace, tasks: list[str]) -> list[str]:
    if args.agentdebug_installer_config:
        if not args.claude_installer_config:
            raise ValueError(
                '--agentdebug-installer-config requires --claude-installer-config'
            )
        if args.environment_backend != 'docker':
            raise ValueError(
                'AgentDebugX CLI provisioning currently supports only Docker'
            )
        agent = AGENTDEBUG_CLAUDE_AGENT
    else:
        agent = PINNED_CLAUDE_AGENT if args.claude_installer_config else args.agent
    cmd = ['harbor', 'run', '-d', DATASET, '-a', agent, '-n', str(args.n_concurrent)]
    for name in tasks:
        cmd += ['-i', name]
    if args.model:
        cmd += ['-m', args.model]
    # Recorded into the trial's config.json, unlike an env fallback.
    if args.effort:
        cmd += ['--agent-kwarg', f'reasoning_effort={args.effort}']
    for value in args.agent_kwarg or []:
        cmd += ['--agent-kwarg', value]
    if args.claude_installer_config:
        for value in _resolved_installer_kwargs(Path(args.claude_installer_config)):
            cmd += ['--agent-kwarg', value]
    if args.agentdebug_installer_config:
        for value in _resolved_agentdebug_installer_kwargs(
            Path(args.agentdebug_installer_config)
        ):
            cmd += ['--agent-kwarg', value]
    cmd += ['--env', args.environment_backend]
    if args.environment_backend == 'singularity':
        if not args.sif_cache_dir:
            raise ValueError(
                '--sif-cache-dir (or HARBOR_SIF_CACHE_DIR) is required '
                'for the singularity backend'
            )
        cmd += [
            '--environment-kwarg',
            f'singularity_image_cache_dir={args.sif_cache_dir}',
        ]
    else:
        # Harbor's default Docker cleanup includes ``--rmi local``.  The
        # evaluation host has a strict image-retention policy, so select the
        # branch that runs plain ``docker compose down``: completed containers
        # and networks go away, while every downloaded/built image is retained.
        cmd.append('--no-delete')
    cmd += ['--jobs-dir', args.jobs_dir, '--yes']
    if args.install_only:
        cmd.append('--install-only')
    # Method-specific injection: advice files and the AgentDebugX skill bundle.
    for path in args.extra_instruction_path or []:
        cmd += ['--extra-instruction-path', path]
    for path in args.skill or []:
        cmd += ['--skill', path]
    # Recovery methods (rerun, rerun-deep, rerun-adamast, rerun-skill): seed the
    # agent's own prior conversation, mount an immutable diagnostic-input
    # copy of it, and pass through only the named env vars a diagnosis needs
    # (docs/architecture.md, "Recommended data flow" and "Credentials and
    # network").
    if args.load_trajectory:
        cmd += ['--load-trajectory', args.load_trajectory]
    if args.mount:
        cmd += ['--mounts', _build_mounts_json(args.mount)]
    for entry in _resolve_agent_env(args.agent_env or []):
        cmd += ['--agent-env', entry]
    return cmd


def cmd_run(args: argparse.Namespace) -> int:
    if args.task:
        tasks = [t if t.startswith(TASK_PREFIX) else TASK_PREFIX + t for t in args.task]
    else:
        tasks = _read_tasks(Path(args.tasks_file))
    if not tasks:
        print('no tasks to run', file=sys.stderr)
        return 2

    cmd = _build_run_command(args, tasks)

    print('+ ' + _loggable_command(cmd), file=sys.stderr)
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[2])
    env['PYTHONPATH'] = os.pathsep.join(
        part for part in (repo_root, env.get('PYTHONPATH')) if part
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    sys.stderr.write(proc.stderr)

    log_path = Path(args.jobs_dir) / (
        f'{args.environment_backend}.{args.method}.harbor.log'
    )
    log_path.write_text(proc.stdout + '\n' + proc.stderr, encoding='utf-8')

    job_dir = _job_dir_from_output(proc.stdout) or _job_dir_from_output(proc.stderr)
    if not job_dir:
        print(f'could not find job dir in harbor output; see {log_path}', file=sys.stderr)
        return 1
    print(job_dir)
    return 0


def _trial_record(trial_dir: Path) -> dict | None:
    result_path = trial_dir / 'result.json'
    if not result_path.is_file():
        return None
    result = json.loads(result_path.read_text(encoding='utf-8'))
    config_path = trial_dir / 'config.json'
    config = (
        json.loads(config_path.read_text(encoding='utf-8'))
        if config_path.is_file()
        else (result.get('config') or {})
    )

    rewards = ((result.get('verifier_result') or {}).get('rewards')) or {}
    reward = rewards.get('reward')
    exception = result.get('exception_info')

    agent_dir = trial_dir / 'agent'
    sessions = sorted(
        str(p)
        for p in (agent_dir / 'sessions').rglob('*.jsonl')
        if 'subagents' not in p.parts
    )
    trajectory = agent_dir / 'trajectory.json'
    environment = config.get('environment') or {}

    return {
        'task': result.get('task_name'),
        'trial': result.get('trial_name'),
        # None reward means the trial errored before the verifier ran.
        'reward': reward,
        'resolved': reward == 1.0,
        'errored': exception is not None,
        'exception': (exception or {}).get('exception_type') if isinstance(exception, dict) else exception,
        'environment_backend': environment.get('type'),
        'trial_dir': str(trial_dir),
        'trajectory': str(trajectory) if trajectory.is_file() else None,
        'sessions': sessions,
    }


def cmd_collect(args: argparse.Namespace) -> int:
    job_dir = Path(args.job_dir)
    records = []
    for trial_dir in sorted(job_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        record = _trial_record(trial_dir)
        if record:
            records.append(record)

    out = Path(args.out) if args.out else None
    lines = '\n'.join(json.dumps(r) for r in records)
    if out:
        out.write_text(lines + '\n', encoding='utf-8')
    else:
        print(lines)

    resolved = sum(1 for r in records if r['resolved'])
    errored = sum(1 for r in records if r['errored'])
    print(
        f'{len(records)} trials: {resolved} resolved, '
        f'{len(records) - resolved - errored} unresolved, {errored} errored',
        file=sys.stderr,
    )
    return 0


ADVICE_TEMPLATE = """# Prior attempt failed — diagnosis from AgentDebugX

A previous attempt at this exact task failed. AgentDebugX analyzed that
trajectory with its deep-debug analyzer. Its findings:

## Root cause

{summary}

## Recommended next attempt

{action}

## How to use this

Treat the above as evidence about what went wrong last time, not as a complete
plan. You are explicitly authorized to act on it in this workspace: make the
edits and run the commands you judge necessary to complete the task. Do not ask
for confirmation — this run is non-interactive and no one can answer.
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print('+ ' + ' '.join(cmd), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Ingest one failed trial's session, deep-diagnose it, render advice."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or Path(args.sessions).stem

    trajectory = out_dir / f'{name}.trajectory.json'
    report = out_dir / f'{name}.deep.json'
    advice = out_dir / f'{name}.advice.md'

    proc = _run([
        AGENTDEBUG_BIN, 'ingest', args.sessions,
        '--format', 'claude_code', '--out', str(trajectory),
    ])
    if proc.returncode != 0 or not trajectory.is_file():
        sys.stderr.write(proc.stderr)
        return 1

    # deepdebug does not depend on the flags on attributor; hence set to none.
    proc = _run([
        AGENTDEBUG_BIN, 'diagnose', str(trajectory),
        '--mode', 'deepdebug',
        '--attributor', 'none',
        '--recovery', 'self_refine',
        '--out', str(report), '--no-color',
    ])
    if proc.returncode != 0 or not report.is_file():
        sys.stderr.write(proc.stderr)
        return 1

    data = json.loads(report.read_text(encoding='utf-8'))
    primary = (data.get('recovery') or {}).get('primary') or {}
    action = primary.get('suggestion_text') or '\n'.join(data.get('suggestions') or [])
    advice.write_text(
        ADVICE_TEMPLATE.format(summary=data.get('summary') or '(none)', action=action or '(none)'),
        encoding='utf-8',
    )
    print(advice)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p_run = sub.add_parser('run', help='Launch one method and print its job dir')
    p_run.add_argument('--method', required=True, help='Method label, used to name the log')
    p_run.add_argument('--tasks-file', help='One task name per line')
    p_run.add_argument('--task', action='append', help='Run this task only; repeatable')
    p_run.add_argument('--agent', default='oracle')
    p_run.add_argument('--model', default=os.environ.get('TB_MODEL'))
    p_run.add_argument(
        '--effort',
        default=os.environ.get('TB_EFFORT'),
        choices=['low', 'medium', 'high', 'xhigh', 'max', None],
        help='Claude Code reasoning effort (--effort)',
    )
    p_run.add_argument('--n-concurrent', type=int, default=4)
    p_run.add_argument(
        '--environment-backend',
        choices=ENVIRONMENT_BACKENDS,
        default=os.environ.get('TB_ENVIRONMENT', 'singularity'),
        help='Harbor runtime backend (default: TB_ENVIRONMENT or singularity)',
    )
    p_run.add_argument('--jobs-dir', default=os.environ.get('HARBOR_JOBS_DIR', 'jobs'))
    p_run.add_argument('--sif-cache-dir', default=os.environ.get('HARBOR_SIF_CACHE_DIR', ''))
    p_run.add_argument('--extra-instruction-path', action='append')
    p_run.add_argument('--skill', action='append')
    p_run.add_argument('--agent-kwarg', action='append')
    p_run.add_argument(
        '--load-trajectory',
        help='Resume the agent from this prior native Claude session '
        '(rerun, rerun-deep, rerun-adamast, rerun-skill)',
    )
    p_run.add_argument(
        '--mount',
        action='append',
        help='SRC:DST bind mount, e.g. an immutable diagnostic-input copy; repeatable',
    )
    p_run.add_argument(
        '--agent-env',
        action='append',
        help='Pass this env var through to the agent by name; repeatable',
    )
    p_run.add_argument('--claude-installer-config')
    p_run.add_argument(
        '--agentdebug-installer-config',
        help='Pinned AgentDebugX wheel config; selects the Docker-only custom '
        'Claude + AgentDebugX agent and requires --claude-installer-config',
    )
    p_run.add_argument('--install-only', action='store_true')
    p_run.set_defaults(func=cmd_run)

    p_collect = sub.add_parser('collect', help='Emit one JSONL record per trial')
    p_collect.add_argument('job_dir')
    p_collect.add_argument('--out', help='Write JSONL here instead of stdout')
    p_collect.set_defaults(func=cmd_collect)

    p_diag = sub.add_parser('diagnose', help='Deep-diagnose a failed trial, render advice')
    p_diag.add_argument('sessions', help='Path to the trial\'s agent/sessions/*.jsonl')
    p_diag.add_argument('--name', help='Basename for the generated files')
    p_diag.add_argument(
        '--out-dir',
        default=os.environ.get('AGENTDEBUG_EVAL_DIR', 'agentdebug-eval'),
    )
    p_diag.set_defaults(func=cmd_diagnose)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
