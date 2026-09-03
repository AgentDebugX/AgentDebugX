"""Managed, readiness-checked local UI process."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore
from agentdebug.workbench.registry import RunRegistry


@dataclass
class UIHandle:
    status: str
    reused: bool
    base_url: Optional[str] = None
    run_url: Optional[str] = None
    pid: Optional[int] = None
    state_file: Optional[str] = None
    warning: Optional[str] = None
    error: Optional[str] = None


def _ensure_ui_locked(
    run_id: str, *, run_registry: RunRegistry, host: str = '127.0.0.1',
    port: int = 0, open_browser: bool = False, timeout: float = 10.0,
) -> UIHandle:
    if host not in {'127.0.0.1', 'localhost', '::1'}:
        return UIHandle(status='failed', reused=False, error='managed UI binds to loopback only')
    run = run_registry.load_run(run_id)
    if not run.artifacts.trace_id or not run.artifacts.report_id:
        return UIHandle(status='failed', reused=False, error='run has no completed trajectory and report')
    state_file = run_registry.root / 'ui' / 'state.json'
    state_file.parent.mkdir(parents=True, exist_ok=True)
    compatible = {
        'manager_version': 1,
        'run_root': str(run_registry.root),
        'store_type': run.artifacts.store_type,
        'store_path': run.artifacts.store_path,
    }
    state = _read_state(state_file)
    if state and all(state.get(k) == v for k, v in compatible.items()) and _healthy(state.get('base_url')):
        handle = _handle(state, run_id, state_file, reused=True)
        _maybe_open(handle, open_browser)
        return handle
    if state_file.exists():
        state_file.unlink()
    selected_port = port or _free_port(host)
    base_url = f'http://{host}:{selected_port}'
    cmd = [
        sys.executable, '-m', 'agentdebug.inspect.ui.manager', '--serve',
        '--host', host, '--port', str(selected_port), '--store-type', run.artifacts.store_type,
        '--store-path', run.artifacts.store_path, '--run-root', str(run_registry.root),
    ]
    try:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return UIHandle(status='failed', reused=False, state_file=str(state_file), error=str(exc))
    state = {
        **compatible, 'manager_version': 1, 'base_url': base_url,
        'pid': process.pid, 'started_at': time.time(), 'ready': False,
    }
    _write_state(state_file, state)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            state_file.unlink(missing_ok=True)
            return UIHandle(status='failed', reused=False, pid=process.pid, state_file=str(state_file), error='UI process exited before readiness')
        if _healthy(base_url):
            state['ready'] = True
            state['ready_at'] = time.time()
            _write_state(state_file, state)
            handle = _handle(state, run_id, state_file, reused=False)
            _maybe_open(handle, open_browser)
            return handle
        time.sleep(0.05)
    process.terminate()
    state_file.unlink(missing_ok=True)
    return UIHandle(status='failed', reused=False, pid=process.pid, state_file=str(state_file), error='UI readiness timed out')


def ensure_ui(
    run_id: str, *, run_registry: RunRegistry, host: str = '127.0.0.1',
    port: int = 0, open_browser: bool = False, timeout: float = 10.0,
) -> UIHandle:
    """Serialize competing starts and return only after a readiness probe."""
    lock_path = run_registry.root / 'ui' / 'startup.lock'
    with _startup_lock(lock_path):
        return _ensure_ui_locked(
            run_id, run_registry=run_registry, host=host, port=port,
            open_browser=open_browser, timeout=timeout,
        )


def ui_status(run_registry: RunRegistry) -> UIHandle:
    state_file = run_registry.root / 'ui' / 'state.json'
    state = _read_state(state_file)
    if state and _healthy(state.get('base_url')):
        return UIHandle(status='ready', reused=True, base_url=state['base_url'], pid=state.get('pid'), state_file=str(state_file))
    return UIHandle(status='stopped', reused=False, state_file=str(state_file))


def _handle(
    state: dict[str, Any], run_id: str, state_file: Path, *, reused: bool
) -> UIHandle:
    base = str(state['base_url'])
    return UIHandle(status='ready', reused=reused, base_url=base, run_url=f'{base}/runs/{run_id}', pid=state.get('pid'), state_file=str(state_file))


def _maybe_open(handle: UIHandle, requested: bool) -> None:
    if requested and handle.run_url:
        try:
            if not webbrowser.open(handle.run_url):
                handle.warning = 'browser did not acknowledge the URL'
        except Exception as exc:
            handle.warning = f'browser open failed: {exc}'


def _healthy(base_url: object) -> bool:
    if not isinstance(base_url, str):
        return False
    try:
        with urllib.request.urlopen(base_url + '/healthz', timeout=0.3) as response:
            return response.status == 200
    except Exception:
        return False


def _free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _read_state(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write_state(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


@contextmanager
def _startup_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+', encoding='utf-8')
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback is process-local
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover
            pass
        handle.close()


def _serve(args: argparse.Namespace) -> None:
    store = SQLiteTraceStore(args.store_path) if args.store_type == 'sqlite' else JsonlTraceStore(args.store_path)
    from agentdebug.inspect.ui.app import serve
    serve(store, host=args.host, port=args.port, run_registry=RunRegistry(args.run_root))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--store-type', choices=('sqlite', 'jsonl'), required=True)
    parser.add_argument('--store-path', required=True)
    parser.add_argument('--run-root', required=True)
    options = parser.parse_args()
    _serve(options)
