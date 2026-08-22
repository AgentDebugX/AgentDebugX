"""Run one task through repeated attempts, with or without AgentDebugX advice.

This is the experiment proper. Both methods get the SAME attempt budget, so the
comparison measures the advice, not the extra tries:

    --method control   attempt, attempt, attempt ...      (no advice ever)
    --method deep      attempt, diagnose, attempt, ...    (advice before each retry)

Each retry in the ``deep`` method re-diagnoses the trajectory that *just* failed
rather than replaying the first diagnosis, and carries the earlier advice
forward so the agent does not re-enter a dead end it already tried. Repeating a
failed approach is the single most common multi-attempt failure mode, and
without carry-forward the loop invites exactly that.

Stops early on success. Emits one JSON summary per task to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_EVAL = HERE / 'run_eval.py'
INSTALLER_CONFIG = Path(
    os.environ.get('CLAUDE_INSTALLER_CONFIG', HERE / 'claude_installer.yaml')
)

CARRY_FORWARD_HEADER = """
## Approaches already tried and still failing

Each item is a previous attempt's diagnosis. Do not repeat these approaches.

{prior}
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print('+ ' + ' '.join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def _attempt(
    task: str,
    method: str,
    attempt: int,
    advice: Path | None,
    environment_backend: str,
) -> dict:
    """Run one harbor attempt; return the collected trial record."""
    cmd = [
        sys.executable, str(RUN_EVAL), 'run',
        '--method', f'{method}-{attempt}',
        '--task', task,
        '--agent', 'claude-code',
        '--claude-installer-config', str(INSTALLER_CONFIG),
        '--n-concurrent', '1',
        '--environment-backend', environment_backend,
    ]
    if advice:
        cmd += ['--extra-instruction-path', str(advice)]

    proc = _run(cmd)
    job_dir = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''
    if not job_dir or not Path(job_dir).is_dir():
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f'attempt {attempt}: no job dir from harbor')

    collect = _run([sys.executable, str(RUN_EVAL), 'collect', job_dir])
    records = [json.loads(line) for line in collect.stdout.splitlines() if line.strip()]
    if not records:
        raise RuntimeError(f'attempt {attempt}: no trial records in {job_dir}')
    return records[0]


def _diagnose(record: dict, task: str, attempt: int, out_dir: Path) -> Path | None:
    """Deep-diagnose the trajectory that just failed; return the advice file."""
    if not record.get('sessions'):
        print(f'attempt {attempt}: no session log to diagnose', file=sys.stderr)
        return None
    proc = _run([
        sys.executable, str(RUN_EVAL), 'diagnose', record['sessions'][0],
        '--name', f'{task}.attempt{attempt}', '--out-dir', str(out_dir),
    ])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return None
    advice = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''
    return Path(advice) if advice and Path(advice).is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--method', choices=['control', 'deep'], required=True)
    parser.add_argument(
        '--environment-backend',
        choices=['docker', 'singularity'],
        default=os.environ.get('TB_ENVIRONMENT', 'singularity'),
    )
    parser.add_argument(
        '--max-retries',
        type=int,
        default=int(os.environ.get('TB_MAX_RETRIES', '3')),
        help='Attempts after the first one',
    )
    parser.add_argument(
        '--out-dir',
        default=os.environ.get('AGENTDEBUG_EVAL_DIR', 'agentdebug-eval'),
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[dict] = []
    prior_advice: list[str] = []
    advice: Path | None = None

    for attempt in range(args.max_retries + 1):
        record = _attempt(
            args.task,
            args.method,
            attempt,
            advice,
            args.environment_backend,
        )
        attempts.append({
            'attempt': attempt,
            'reward': record.get('reward'),
            'resolved': record.get('resolved'),
            'advice_used': str(advice) if advice else None,
            'trial_dir': record.get('trial_dir'),
        })
        if record.get('resolved'):
            break
        if attempt == args.max_retries:
            break

        if args.method == 'control':
            # Same budget, never any advice — that is the whole point of A0.
            continue

        new_advice = _diagnose(record, args.task, attempt, out_dir)
        if not new_advice:
            advice = None
            continue
        text = new_advice.read_text(encoding='utf-8')
        if prior_advice:
            text += CARRY_FORWARD_HEADER.format(prior='\n\n---\n\n'.join(prior_advice))
        merged = out_dir / f'{args.task}.{args.method}.attempt{attempt + 1}.advice.md'
        merged.write_text(text, encoding='utf-8')
        prior_advice.append(new_advice.read_text(encoding='utf-8'))
        advice = merged

    summary = {
        'task': args.task,
        'method': args.method,
        'max_retries': args.max_retries,
        'n_attempts': len(attempts),
        'resolved': any(a['resolved'] for a in attempts),
        'resolved_at_attempt': next(
            (a['attempt'] for a in attempts if a['resolved']), None
        ),
        'attempts': attempts,
    }
    print(json.dumps(summary, indent=2))
    (out_dir / f'{args.task}.{args.method}.summary.json').write_text(
        json.dumps(summary, indent=2), encoding='utf-8'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
