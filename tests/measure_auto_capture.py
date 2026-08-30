"""Reproducible small-scale auto-capture measurements (no model calls)."""

from __future__ import annotations

import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from agentdebug import __version__
from agentdebug.integrations.capture_management import (
    disable_capture_consent,
    enable_capture_consent,
)

ROOT = Path(__file__).resolve().parents[1]
REPETITIONS = 20


def _records(host: str, session_id: str, count: int, revision: str) -> List[Dict[str, Any]]:
    if host == 'claude':
        rows: List[Dict[str, Any]] = []
        parent = None
        for index in range(count):
            role = 'user' if index % 2 == 0 else 'assistant'
            uuid = f'{role}-{index}'
            timestamp = f'2026-08-26T12:{index // 60:02d}:{index % 60:02d}Z'
            row: Dict[str, Any] = {
                'type': role,
                'sessionId': session_id,
                'uuid': uuid,
                'timestamp': timestamp,
                'message': {
                    'role': role,
                    'content': f'{role} message {index} revision {revision if index == count - 1 else "base"}',
                },
            }
            if parent:
                row['parentUuid'] = parent
            rows.append(row)
            parent = uuid
        return rows

    rows = [
        {
            'timestamp': '2026-08-26T12:00:00Z',
            'type': 'session_meta',
            'payload': {
                'id': session_id,
                'cwd': '/synthetic/project',
                'cli_version': '0.149.1',
                'source': 'cli',
                'model_provider': 'openai',
            },
        }
    ]
    for index in range(count):
        role = 'user' if index % 2 == 0 else 'assistant'
        content_type = 'input_text' if role == 'user' else 'output_text'
        timestamp = f'2026-08-26T12:{index // 60:02d}:{index % 60:02d}Z'
        rows.append(
            {
                'timestamp': timestamp,
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': role,
                    'id': f'message-{index}',
                    'content': [
                        {
                            'type': content_type,
                            'text': f'{role} message {index} revision {revision if index == count - 1 else "base"}',
                        }
                    ],
                },
            }
        )
    return rows


def _write_records(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text(
        ''.join(json.dumps(row, separators=(',', ':')) + '\n' for row in rows),
        encoding='utf-8',
    )


def _payload(host: str, event: str, session_id: str, transcript: Path, project: Path, revision: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'hook_event_name': event,
        'session_id': session_id,
        'transcript_path': str(transcript),
        'cwd': str(project),
    }
    if event in {'Stop', 'UserPromptSubmit'} and host == 'codex':
        payload['turn_id'] = f'turn-{revision}'
    if event == 'TaskCompleted':
        payload.update({'task_id': f'task-{revision}', 'task_subject': 'synthetic checkpoint'})
    if event == 'SessionEnd':
        payload['reason'] = 'other'
    return payload


def _dispatch_command(host: str) -> List[str]:
    return [
        sys.executable,
        '-m',
        'agentdebug.cli',
        'integrations',
        'capture',
        'dispatch',
        '--platform',
        host,
    ]


def _dispatch(host: str, project: Path, payload: Dict[str, Any]) -> float:
    started = time.perf_counter()
    completed = subprocess.run(
        _dispatch_command(host),
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        check=False,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode != 0 or completed.stdout or completed.stderr:
        raise RuntimeError(
            f'dispatch was not silent/fail-open: rc={completed.returncode} '
            f'stdout={completed.stdout!r} stderr={completed.stderr!r}'
        )
    return elapsed


def _summary(samples: List[float]) -> Dict[str, float]:
    ordered = sorted(samples)
    p95_index = max(0, int(0.95 * len(ordered) + 0.999999) - 1)
    return {
        'median_ms': round(statistics.median(ordered), 3),
        'p95_ms': round(ordered[p95_index], 3),
    }


def measure_latency(host: str, transcript_root: Path) -> List[Dict[str, Any]]:
    events = ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd']
    if host == 'claude':
        events.insert(3, 'TaskCompleted')
    results = []
    for count in (10, 50, 100):
        for event in events:
            with tempfile.TemporaryDirectory(prefix='agentdebug-measure-project-') as raw_project:
                project = Path(raw_project)
                session_id = f'{host}-{event.lower()}-{count}'
                with tempfile.TemporaryDirectory(
                    prefix='agentdebug-measure-session-', dir=transcript_root
                ) as raw_session:
                    transcript = Path(raw_session) / 'transcript.jsonl'
                    _write_records(transcript, _records(host, session_id, count, 'initial'))
                    enable_capture_consent(host, project)
                    if event in {'SessionStart', 'UserPromptSubmit'}:
                        _dispatch(
                            host,
                            project,
                            _payload(host, 'Stop', session_id, transcript, project, 'seed'),
                        )
                    _dispatch(
                        host,
                        project,
                        _payload(host, event, session_id, transcript, project, 'warmup'),
                    )
                    samples = []
                    for repetition in range(REPETITIONS):
                        revision = f'{repetition:04d}'
                        if event not in {'SessionStart', 'UserPromptSubmit'}:
                            _write_records(
                                transcript,
                                _records(host, session_id, count, revision),
                            )
                        samples.append(
                            _dispatch(
                                host,
                                project,
                                _payload(host, event, session_id, transcript, project, revision),
                            )
                        )
                    results.append(
                        {
                            'host': host,
                            'event': event,
                            'events': count,
                            'repetitions': REPETITIONS,
                            **_summary(samples),
                        }
                    )
    return results


def measure_disabled(transcript_root: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='agentdebug-measure-disabled-') as raw_project:
        project = Path(raw_project)
        with tempfile.TemporaryDirectory(
            prefix='agentdebug-measure-session-', dir=transcript_root
        ) as raw_session:
            transcript = Path(raw_session) / 'transcript.jsonl'
            _write_records(transcript, _records('claude', 'disabled-session', 10, 'base'))
            enable_capture_consent('claude', project)
            disable_capture_consent('claude', project)
            payload = _payload('claude', 'Stop', 'disabled-session', transcript, project, 'base')
            _dispatch('claude', project, payload)
            samples = [_dispatch('claude', project, payload) for _ in range(REPETITIONS)]
            return {
                'host': 'claude',
                'event': 'disabled dispatch',
                'events': None,
                'repetitions': REPETITIONS,
                **_summary(samples),
            }


def _database_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm'), Path(str(path) + '-journal'))
        if candidate.exists()
    )


def measure_growth(host: str, transcript_root: Path) -> List[Dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix='agentdebug-growth-project-') as raw_project:
        project = Path(raw_project)
        session_id = f'{host}-growth-session'
        with tempfile.TemporaryDirectory(
            prefix='agentdebug-growth-session-', dir=transcript_root
        ) as raw_session:
            transcript = Path(raw_session) / 'transcript.jsonl'
            enabled = enable_capture_consent(host, project)
            database = Path(enabled['store_path'])
            rows = [
                {
                    'host': host,
                    'capture': 0,
                    'events': 0,
                    'trajectory_payload_bytes': 0,
                    'db_plus_journal_bytes': _database_bytes(database),
                    'steady_db_bytes': database.stat().st_size if database.exists() else 0,
                    'receipts': 0,
                    'sessions': 0,
                }
            ]
            for capture in range(1, 11):
                count = capture * 10
                _write_records(
                    transcript, _records(host, session_id, count, f'capture-{capture}')
                )
                _dispatch(
                    host,
                    project,
                    _payload(host, 'Stop', session_id, transcript, project, str(capture)),
                )
                with sqlite3.connect(database) as connection:
                    payload_bytes = connection.execute(
                        'SELECT length(payload_json) FROM trajectories'
                    ).fetchone()[0]
                    receipt_count = connection.execute(
                        'SELECT COUNT(*) FROM capture_receipts'
                    ).fetchone()[0]
                    session_count = connection.execute(
                        'SELECT COUNT(*) FROM capture_sessions'
                    ).fetchone()[0]
                rows.append(
                    {
                        'host': host,
                        'capture': capture,
                        'events': count,
                        'trajectory_payload_bytes': payload_bytes,
                        'db_plus_journal_bytes': _database_bytes(database),
                        'steady_db_bytes': database.stat().st_size,
                        'receipts': receipt_count,
                        'sessions': session_count,
                    }
                )
            return rows


def _version(command: List[str]) -> str:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        ).stdout.strip().splitlines()[0]
    except (OSError, IndexError):
        return 'not installed'


def main() -> None:
    claude_root = Path.home() / '.claude' / 'projects'
    codex_root = Path.home() / '.codex' / 'sessions'
    claude_root.mkdir(parents=True, exist_ok=True)
    codex_root.mkdir(parents=True, exist_ok=True)
    environment = {
        'date': time.strftime('%Y-%m-%d'),
        'commit': _version(['git', 'rev-parse', 'HEAD']),
        'python': sys.version.split()[0],
        'agentdebugx': __version__,
        'claude_code': _version(['claude', '--version']),
        'codex': _version(['codex', '--version']),
        'os': _version(['uname', '-srmo']),
        'filesystem': _version(['stat', '-f', '-c', '%T', str(ROOT)]),
        'sqlite': sqlite3.sqlite_version,
        'journal_mode': 'delete',
    }
    payload = {
        'environment': environment,
        'latency': [
            measure_disabled(claude_root),
            *measure_latency('claude', claude_root),
            *measure_latency('codex', codex_root),
        ],
        'growth': [
            *measure_growth('claude', claude_root),
            *measure_growth('codex', codex_root),
        ],
    }
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
