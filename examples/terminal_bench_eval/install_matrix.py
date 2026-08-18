from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _cached_sif(cache_dir: Path, task: str) -> Path | None:
    short_name = task.removeprefix('terminal-bench/')
    matches = sorted(cache_dir.glob(f'*_{short_name}_*.sif'))
    direct = cache_dir / f'{short_name}.sif'
    if direct.is_file():
        matches.append(direct)
    return matches[0] if matches else None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'expected a JSON object in {path}')
    return payload


def build_install_matrix(job_dir: Path, sif_cache_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for trial_dir in sorted(job_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        result = _read_json(trial_dir / 'result.json')
        if result is None:
            continue

        task = result.get('task_name')
        if not isinstance(task, str):
            raise ValueError(f'missing task_name in {trial_dir / "result.json"}')
        metadata = _read_json(trial_dir / 'agent' / 'claude-install.json') or {}
        exception = result.get('exception_info') or {}
        error_type = exception.get('exception_type') or metadata.get('error_type')
        exception_message = exception.get('exception_message') or ''
        agent_config = (result.get('config') or {}).get('agent') or {}
        agent_kwargs = agent_config.get('kwargs') or {}
        configured_sha256 = agent_kwargs.get('claude_artifact_sha256')
        sif_path = _cached_sif(sif_cache_dir, task)

        if metadata.get('status') == 'success' and not exception:
            status = 'success'
            failure_class = None
        else:
            status = 'failed'
            if (
                (error_type and error_type.startswith('Environment'))
                or 'Server process died' in exception_message
                or 'Failed to convert Docker image' in exception_message
            ):
                failure_class = 'image_start_or_conversion'
            else:
                failure_class = metadata.get('failure_class') or 'agent_installation'

        rows.append(
            {
                'task': task,
                'sif_status': 'cached' if sif_path else 'missing',
                'sif_path': str(sif_path) if sif_path else None,
                'status': status,
                'claude_version': metadata.get('claude_version'),
                'artifact_sha256': metadata.get('artifact_sha256')
                or configured_sha256,
                'installation_time_seconds': metadata.get(
                    'installation_time_seconds'
                ),
                'failure_class': failure_class,
                'error_type': error_type,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Collect a pinned-Claude install-only Harbor job as JSONL.'
    )
    parser.add_argument('job_dir', type=Path)
    parser.add_argument('--sif-cache-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()

    rows = build_install_matrix(args.job_dir, args.sif_cache_dir)
    args.out.write_text(
        ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows),
        encoding='utf-8',
    )
    failures = sum(row['status'] != 'success' for row in rows)
    print(f'{len(rows)} rows: {len(rows) - failures} successful, {failures} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
