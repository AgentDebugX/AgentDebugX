"""Seed -> rerun/rerun-deep recovery-method orchestration for the
Terminal-Bench recovery study, across one or many tasks.

For each task: reuse an already-completed Seed trial if one was given,
otherwise launch Seed fresh; classify the result; and, for an eligible
failure, run whichever recovery method(s) were requested:

  rerun       prior conversation + a neutral verifier-failure notice, no
              diagnosis
  rerun-deep  prior conversation + a host-generated AgentDebugX report
              (``run_eval.py diagnose``) on the identical diagnostic input

Both load the *same* diagnostic input via ``--load-trajectory`` so the only
difference between them is the treatment text in
``--extra-instruction-path`` — that difference is what rerun-deep minus
rerun measures. ``--method seed`` alone launches/classifies Seed only, no
recovery method.

A single task is just a task list of length one — there is no separate
"batch mode". Reusing an existing Seed trial (``seed_trial_dir`` in a
``--tasks-config`` entry, or the ``--seed-trial-dir`` flag for a single
task) is validated against the current run's model/effort/installer before
use; a mismatch aborts the whole run rather than silently mixing
configurations (docs/EXPERIMENT_PROTOCOL.md, "Fixed configuration").

rerun-adamast and rerun-skill (in-container AgentDebugX skill) are not
implemented here; see docs/architecture.md and the status table in
README.md.

Command construction (``classify_seed_result``, ``build_resume_run_args``,
``build_seed_run_args``, ``rerun_notice_text``, task-list parsing) is pure
and offline-testable. Only ``run_task``/``main`` shell out to
``run_eval.py`` and, through it, to Harbor and agentdebug.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
RUN_EVAL = HERE / 'run_eval.py'
TASK_PREFIX = 'terminal-bench/'

if __package__:
    from .claude_artifact import ClaudeInstallerConfig
    from .session_selection import DiagnosticInput, copy_diagnostic_input, find_primary_session
else:
    from claude_artifact import ClaudeInstallerConfig  # type: ignore[no-redef]
    from session_selection import (  # type: ignore[no-redef]
        DiagnosticInput,
        copy_diagnostic_input,
        find_primary_session,
    )

# A Seed reward of 0.0 with no harness/agent exception is the only outcome
# the recovery study evaluates (EXPERIMENT_PROTOCOL.md, "Evaluation
# population" and "Outcome classification"). Everything else — resolved,
# errored, or missing reward — is excluded rather than treated as an
# unresolved agent attempt.
SEED_RESOLVED = 'resolved'
SEED_ELIGIBLE_FAILURE = 'eligible_failure'
SEED_EXCLUSION = 'exclusion'

RERUN_NOTICE = """# Prior attempt did not pass verification

A previous attempt at this exact task did not pass the hidden verifier. The
task filesystem is fresh, but your prior conversation is loaded below.
Reconsider your approach and finish the task correctly.

You are explicitly authorized to make the edits and run the commands you
judge necessary in this workspace. Do not ask for confirmation — this run is
non-interactive and no one can answer.
"""


class SeedConfigMismatch(RuntimeError):
    """A reused Seed trial's recorded config doesn't match this run's."""


@dataclass(frozen=True)
class TaskSpec:
    """One task to process: a fresh Seed, or an existing trial to reuse."""

    task: str
    seed_trial_dir: str | None = None


def _prefixed(task: str) -> str:
    return task if task.startswith(TASK_PREFIX) else TASK_PREFIX + task


def classify_seed_result(record: dict) -> str:
    """Classify one Seed trial record (the shape ``run_eval.py collect`` emits)."""
    if record.get('errored'):
        return SEED_EXCLUSION
    reward = record.get('reward')
    if reward == 1.0:
        return SEED_RESOLVED
    if reward == 0.0:
        return SEED_ELIGIBLE_FAILURE
    return SEED_EXCLUSION


def diagnostic_input_name(task: str) -> str:
    """Filesystem-safe basename for a task's diagnostic input files."""
    return task.replace('terminal-bench/', '').replace('/', '_')


def seed_run_dir(out_dir: Path, task: str, seed_trial_dir: Path) -> Path:
    """Per-run artifact dir: ``<out_dir>/<task>/<seed-trial-name>/``.

    Keyed by the Seed trial's own (harbor-generated, effectively unique)
    trial name rather than just the task, so a second Seed run on the same
    task gets its own artifact tree instead of silently overwriting the
    first run's diagnostic input, advice, and summary.
    """
    return out_dir / diagnostic_input_name(task) / seed_trial_dir.name


def select_seed_diagnostic_input(seed_trial_dir: Path, run_dir: Path) -> DiagnosticInput:
    """Select the Seed trial's primary session and copy it as diagnostic input.

    This must run once per eligible Seed failure, before any recovery method
    starts, so rerun and rerun-deep both load the identical immutable copy
    rather than racing a session a resumed Claude conversation is still
    appending to.
    """
    session = find_primary_session(seed_trial_dir / 'agent')
    return copy_diagnostic_input(session, run_dir, name='diagnostic-input')


def rerun_notice_text() -> str:
    return RERUN_NOTICE


def read_tasks_file(path: Path) -> list[TaskSpec]:
    """Plain text, one task per line; blank lines and ``#`` comments ignored.

    Every task gets a fresh Seed — use ``--tasks-config`` (YAML) to reuse an
    existing Seed trial for some tasks.
    """
    specs = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        specs.append(TaskSpec(task=_prefixed(line)))
    return specs


def parse_tasks_config(path: Path) -> list[TaskSpec]:
    """YAML task list. Each entry is either a bare task name (fresh Seed) or
    a mapping ``{task: ..., seed_trial_dir: ...}`` to reuse an existing,
    already-completed Seed trial instead of launching a new one.

        tasks:
          - terminal-bench/sqlite-db-truncate
          - task: terminal-bench/raman-fitting
            seed_trial_dir: /path/to/harbor-jobs/<job>/raman-fitting__abc
    """
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    entries = data.get('tasks') if isinstance(data, dict) else data
    specs = []
    for entry in entries or []:
        if isinstance(entry, str):
            specs.append(TaskSpec(task=_prefixed(entry)))
        else:
            specs.append(TaskSpec(
                task=_prefixed(entry['task']),
                seed_trial_dir=entry.get('seed_trial_dir'),
            ))
    return specs


def check_seed_config_matches(
    seed_trial_dir: Path,
    *,
    model: str | None,
    effort: str | None,
    claude_installer_config: str | None,
) -> None:
    """Raise SeedConfigMismatch if a reused Seed trial's recorded config
    differs from this run's model/effort/installer.

    The protocol requires identical configuration across Seed and every
    recovery method for a task (docs/EXPERIMENT_PROTOCOL.md, "Fixed
    configuration") — silently reusing a Seed attempt from a different
    model or Claude build would invalidate that guarantee without anyone
    noticing.
    """
    config_path = seed_trial_dir / 'config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    agent = config.get('agent') or {}
    kwargs = agent.get('kwargs') or {}
    recorded_model = agent.get('model_name')
    recorded_effort = kwargs.get('reasoning_effort')
    recorded_sha256 = kwargs.get('claude_artifact_sha256')

    mismatches = []
    if model and recorded_model and model != recorded_model:
        mismatches.append(f'model: reused={recorded_model!r} requested={model!r}')
    if effort and recorded_effort and effort != recorded_effort:
        mismatches.append(f'effort: reused={recorded_effort!r} requested={effort!r}')
    if claude_installer_config and recorded_sha256:
        expected_sha256 = ClaudeInstallerConfig.load(Path(claude_installer_config)).artifact.sha256
        if expected_sha256 != recorded_sha256:
            mismatches.append(
                f'claude artifact sha256: reused={recorded_sha256!r} requested={expected_sha256!r}'
            )
    if mismatches:
        raise SeedConfigMismatch(
            f'{seed_trial_dir}: reused Seed trial config does not match this run:\n  '
            + '\n  '.join(mismatches)
        )


def build_seed_run_args(
    task: str,
    *,
    jobs_dir: str,
    sif_cache_dir: str,
    model: str | None = None,
    effort: str | None = None,
    claude_installer_config: str | None = None,
) -> list[str]:
    """Argv for ``run_eval.py run`` to launch a fresh Seed attempt."""
    args = [
        'run',
        '--arm', 'seed',
        '--task', task,
        '--agent', 'claude-code',
        '--n-concurrent', '1',
        '--jobs-dir', jobs_dir,
        '--sif-cache-dir', sif_cache_dir,
    ]
    if model:
        args += ['--model', model]
    if effort:
        args += ['--effort', effort]
    if claude_installer_config:
        args += ['--claude-installer-config', claude_installer_config]
    return args


def build_resume_run_args(
    *,
    method: str,
    task: str,
    diagnostic_input: DiagnosticInput,
    instruction_path: Path,
    jobs_dir: str,
    sif_cache_dir: str,
    model: str | None = None,
    effort: str | None = None,
    claude_installer_config: str | None = None,
) -> list[str]:
    """Argv for ``run_eval.py run`` (excluding the interpreter and script path).

    Every recovery method gets exactly one attempt (protocol: "maximum of
    one recovery attempt per arm"), the same fixed configuration, and loads
    the same diagnostic input via ``--load-trajectory`` — only the treatment
    in ``--extra-instruction-path`` differs between methods.
    """
    args = [
        'run',
        '--arm', method,
        '--task', task,
        '--agent', 'claude-code',
        '--n-concurrent', '1',
        '--jobs-dir', jobs_dir,
        '--sif-cache-dir', sif_cache_dir,
        '--load-trajectory', str(diagnostic_input.path),
        '--extra-instruction-path', str(instruction_path),
    ]
    if model:
        args += ['--model', model]
    if effort:
        args += ['--effort', effort]
    if claude_installer_config:
        args += ['--claude-installer-config', claude_installer_config]
    return args


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print('+ ' + ' '.join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_eval(args: list[str]) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(RUN_EVAL)] + args)


def _last_stdout_line(proc: subprocess.CompletedProcess) -> str:
    lines = proc.stdout.strip().splitlines()
    return lines[-1] if lines else ''


def _run_seed(task: str, **kwargs) -> dict:
    """Launch a fresh Seed attempt for one task and return its trial record."""
    proc = _run_eval(build_seed_run_args(task, **kwargs))
    job_dir = _last_stdout_line(proc)
    if not job_dir or not Path(job_dir).is_dir():
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f'seed: no job dir from harbor for {task}')

    collect = _run_eval(['collect', job_dir])
    records = [json.loads(line) for line in collect.stdout.splitlines() if line.strip()]
    if not records:
        raise RuntimeError(f'seed: no trial records in {job_dir}')
    return records[0]


def _collect_existing_seed(seed_trial_dir: Path) -> dict:
    """Classify an already-completed Seed trial without re-running it."""
    proc = _run_eval(['collect', str(seed_trial_dir.parent)])
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if Path(record['trial_dir']) == seed_trial_dir:
            return record
    raise RuntimeError(f'no trial record found for {seed_trial_dir}')


def _run_resume_method(
    *,
    method: str,
    task: str,
    diagnostic_input: DiagnosticInput,
    instruction_path: Path,
    jobs_dir: str,
    sif_cache_dir: str,
    model: str | None,
    effort: str | None,
    claude_installer_config: str | None,
) -> dict:
    run_args = build_resume_run_args(
        method=method,
        task=task,
        diagnostic_input=diagnostic_input,
        instruction_path=instruction_path,
        jobs_dir=jobs_dir,
        sif_cache_dir=sif_cache_dir,
        model=model,
        effort=effort,
        claude_installer_config=claude_installer_config,
    )
    proc = _run_eval(run_args)
    job_dir = _last_stdout_line(proc)
    if not job_dir or not Path(job_dir).is_dir():
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f'{method}: no job dir from harbor')

    collect = _run_eval(['collect', job_dir])
    records = [json.loads(line) for line in collect.stdout.splitlines() if line.strip()]
    if not records:
        raise RuntimeError(f'{method}: no trial records in {job_dir}')
    return records[0]


def run_task(
    spec: TaskSpec,
    *,
    methods: list[str],
    out_dir: Path,
    jobs_dir: str,
    sif_cache_dir: str,
    model: str | None,
    effort: str | None,
    claude_installer_config: str | None,
) -> dict:
    """Run one task: reuse-or-launch Seed, classify, and run any requested
    recovery methods for an eligible failure. Returns one summary row."""
    common = dict(
        jobs_dir=jobs_dir, sif_cache_dir=sif_cache_dir,
        model=model, effort=effort, claude_installer_config=claude_installer_config,
    )

    if spec.seed_trial_dir:
        seed_trial_dir = Path(spec.seed_trial_dir)
        check_seed_config_matches(
            seed_trial_dir, model=model, effort=effort,
            claude_installer_config=claude_installer_config,
        )
        seed_record = _collect_existing_seed(seed_trial_dir)
    else:
        seed_record = _run_seed(spec.task, **common)

    outcome = classify_seed_result(seed_record)
    row: dict = {
        'task': spec.task,
        'seed_outcome': outcome,
        'seed_reward': seed_record.get('reward'),
        'seed_trial_dir': seed_record.get('trial_dir'),
    }

    resume_methods = [m for m in methods if m != 'seed']
    if not resume_methods or outcome != SEED_ELIGIBLE_FAILURE:
        return row

    seed_trial_dir = Path(seed_record['trial_dir'])
    run_dir = seed_run_dir(out_dir, spec.task, seed_trial_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    diagnostic_input = select_seed_diagnostic_input(seed_trial_dir, run_dir)

    results: dict[str, dict] = {}

    if 'rerun' in resume_methods:
        notice_path = run_dir / 'rerun-notice.md'
        notice_path.write_text(rerun_notice_text(), encoding='utf-8')
        results['rerun'] = _run_resume_method(
            method='rerun', task=spec.task, diagnostic_input=diagnostic_input,
            instruction_path=notice_path, **common,
        )

    if 'rerun-deep' in resume_methods:
        diagnose = _run_eval([
            'diagnose', str(diagnostic_input.path),
            '--name', 'seed',
            '--out-dir', str(run_dir),
        ])
        advice_path_str = _last_stdout_line(diagnose)
        if diagnose.returncode != 0 or not advice_path_str:
            sys.stderr.write(diagnose.stderr)
            print(f'rerun-deep: diagnosis failed for {spec.task}', file=sys.stderr)
        else:
            results['rerun-deep'] = _run_resume_method(
                method='rerun-deep', task=spec.task, diagnostic_input=diagnostic_input,
                instruction_path=Path(advice_path_str), **common,
            )

    summary_path = run_dir / 'rerun-summary.json'
    # Two separate invocations against the same Seed trial (e.g. one run per
    # method) must not clobber each other's already-recorded result, so
    # merge into whatever's already on disk for this run.
    existing_methods = {}
    if summary_path.is_file():
        existing_methods = json.loads(summary_path.read_text(encoding='utf-8')).get('methods', {})

    methods_summary = {
        **existing_methods,
        **{
            method: {
                'reward': record.get('reward'),
                'resolved': record.get('resolved'),
                'trial_dir': record.get('trial_dir'),
            }
            for method, record in results.items()
        },
    }
    summary = {
        'task': spec.task,
        'seed_outcome': outcome,
        'seed_trial_dir': str(seed_trial_dir),
        'diagnostic_input': diagnostic_input.to_metadata(),
        'methods': methods_summary,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')

    row['methods'] = methods_summary
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', help='Run this task (fresh Seed); repeatable')
    parser.add_argument('--tasks-file', help='Plain text, one task per line (fresh Seed each)')
    parser.add_argument(
        '--tasks-config',
        help='YAML task list; entries may set seed_trial_dir to reuse an existing Seed trial',
    )
    parser.add_argument(
        '--seed-trial-dir',
        help='Reuse this existing Seed trial instead of launching one; requires exactly one --task',
    )
    parser.add_argument(
        '--method',
        choices=['seed', 'rerun', 'rerun-deep'],
        action='append',
        dest='methods',
        help=(
            'Recovery method(s) to run per eligible failure; repeatable. '
            "Default: rerun-deep. Pass '--method seed' alone to launch/classify "
            'Seed only, with no recovery method.'
        ),
    )
    parser.add_argument('--jobs-dir', default=os.environ.get('HARBOR_JOBS_DIR', 'jobs'))
    parser.add_argument('--sif-cache-dir', default=os.environ.get('HARBOR_SIF_CACHE_DIR', ''))
    parser.add_argument('--model', default=os.environ.get('TB_MODEL'))
    parser.add_argument('--effort', default=os.environ.get('TB_EFFORT'))
    parser.add_argument(
        '--claude-installer-config',
        default=os.environ.get('CLAUDE_INSTALLER_CONFIG'),
    )
    parser.add_argument(
        '--out-dir',
        default=os.environ.get('AGENTDEBUG_EVAL_DIR', 'agentdebug-eval'),
        help='Diagnostic input, advice, and summary files go here',
    )
    parser.add_argument('--out', help='Write the aggregate JSON summary here instead of stdout')
    args = parser.parse_args()

    sources = [bool(args.task), bool(args.tasks_file), bool(args.tasks_config)]
    if sum(sources) != 1:
        print('pass exactly one of: --task (repeatable), --tasks-file, --tasks-config', file=sys.stderr)
        return 2
    if args.seed_trial_dir and (not args.task or len(args.task) != 1):
        print('--seed-trial-dir requires exactly one --task', file=sys.stderr)
        return 2

    if args.tasks_config:
        specs = parse_tasks_config(Path(args.tasks_config))
    elif args.tasks_file:
        specs = read_tasks_file(Path(args.tasks_file))
    elif args.seed_trial_dir:
        specs = [TaskSpec(task=_prefixed(args.task[0]), seed_trial_dir=args.seed_trial_dir)]
    else:
        specs = [TaskSpec(task=_prefixed(t)) for t in args.task]

    methods = args.methods or ['rerun-deep']
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    common = dict(
        jobs_dir=args.jobs_dir, sif_cache_dir=args.sif_cache_dir,
        model=args.model, effort=args.effort,
        claude_installer_config=args.claude_installer_config,
    )

    results = []
    for spec in specs:
        print(f'=== {spec.task} ===', file=sys.stderr)
        try:
            row = run_task(spec, methods=methods, out_dir=out_dir, **common)
        except SeedConfigMismatch as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except RuntimeError as exc:
            row = {'task': spec.task, 'seed_outcome': 'exclusion', 'error': str(exc)}
        results.append(row)

    rendered = json.dumps(results, indent=2)
    if args.out:
        Path(args.out).write_text(rendered + '\n', encoding='utf-8')
        print(args.out)
    else:
        print(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
