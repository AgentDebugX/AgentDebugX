"""Atomic file registry for DebugRun manifests."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import List

from agentdebug.schema.models import model_to_json, utc_now

from .models import DebugRun


class RunRegistry:
    def __init__(self, root: str = '.agentdebug') -> None:
        self.root = Path(root).expanduser().resolve()
        self.runs_dir = self.root / 'runs'
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, run: DebugRun) -> DebugRun:
        path = self._path(run.run_id)
        if path.exists():
            raise ValueError(f'run already exists: {run.run_id}')
        self._write(path, run)
        return run

    def load_run(self, run_id: str) -> DebugRun:
        path = self._path(run_id)
        if not path.is_file():
            raise KeyError(run_id)
        loader = getattr(DebugRun, 'model_validate_json', None)
        return loader(path.read_text(encoding='utf-8')) if callable(loader) else DebugRun.parse_raw(path.read_text(encoding='utf-8'))

    def update_run(self, run: DebugRun) -> DebugRun:
        current = self.load_run(run.run_id)
        run.created_at = current.created_at
        run.updated_at = utc_now()
        if current.status == 'failed' and run.status in {'running', 'completed'}:
            raise ValueError('a failed run cannot transition to success')
        self._write(self._path(run.run_id), run)
        return run

    def list_runs(self) -> List[DebugRun]:
        paths = [*self.runs_dir.glob('*.json'), *self.root.glob('sessions/*/*/runs/*.json')]
        return sorted((self.load_run(p.stem) for p in paths), key=lambda r: r.created_at, reverse=True)

    def bind_session(self, run_id: str, host: str, session_id: str) -> Path:
        current = self._path(run_id)
        destination = (
            self.root / 'sessions' / _path_component(host)
            / _path_component(session_id) / 'runs' / f'{run_id}.json'
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if current != destination:
            os.replace(current, destination)
        return destination

    def _path(self, run_id: str) -> Path:
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
            raise ValueError('invalid run_id')
        direct = self.runs_dir / f'{run_id}.json'
        if direct.exists():
            return direct
        matches = list(self.root.glob(f'sessions/*/*/runs/{run_id}.json'))
        return matches[0] if matches else direct

    @staticmethod
    def _write(path: Path, run: DebugRun) -> None:
        temp = path.with_suffix(f'.{os.getpid()}.tmp')
        temp.write_text(model_to_json(run, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, path)


def _path_component(value: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9._-]+', value):
        return value
    return hashlib.sha256(value.encode()).hexdigest()
