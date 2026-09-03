from __future__ import annotations

import json

from agentdebug.inspect.ui import manager
from agentdebug.workbench.models import DebugRun, RunArtifactRefs, RunInput
from agentdebug.workbench.profiles import resolve_pipeline
from agentdebug.workbench.registry import RunRegistry


def _registry(tmp_path):
    registry = RunRegistry(str(tmp_path))
    run = DebugRun(
        status='completed', input=RunInput(reference='fixture'), requested_profile='quick',
        resolved_pipeline=resolve_pipeline('quick'),
        artifacts=RunArtifactRefs(trace_id='trace', report_id='report', store_type='sqlite', store_path=str(tmp_path / 'store.sqlite')),
    )
    registry.create_run(run)
    return registry, run


def test_managed_ui_rejects_non_loopback(tmp_path) -> None:
    registry, run = _registry(tmp_path)
    handle = manager.ensure_ui(run.run_id, run_registry=registry, host='0.0.0.0')
    assert handle.status == 'failed'
    assert 'loopback' in handle.error


def test_managed_ui_reuses_compatible_ready_server(tmp_path, monkeypatch) -> None:
    registry, run = _registry(tmp_path)
    state_path = tmp_path / 'ui' / 'state.json'
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({
        'manager_version': 1, 'run_root': str(tmp_path.resolve()), 'store_type': 'sqlite',
        'store_path': str(tmp_path / 'store.sqlite'), 'base_url': 'http://127.0.0.1:1234', 'pid': 42,
    }), encoding='utf-8')
    monkeypatch.setattr(manager, '_healthy', lambda url: True)
    monkeypatch.setattr(manager.webbrowser, 'open', lambda url: False)
    handle = manager.ensure_ui(run.run_id, run_registry=registry, open_browser=True)
    assert handle.status == 'ready'
    assert handle.reused is True
    assert handle.run_url.endswith(f'/runs/{run.run_id}')
    assert handle.warning


def test_managed_ui_timeout_is_structured_and_terminates_child(tmp_path, monkeypatch) -> None:
    registry, run = _registry(tmp_path)

    class Process:
        pid = 99
        terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True

    process = Process()
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *a, **k: process)
    monkeypatch.setattr(manager, '_healthy', lambda url: False)
    handle = manager.ensure_ui(run.run_id, run_registry=registry, timeout=0)
    assert handle.status == 'failed'
    assert handle.error == 'UI readiness timed out'
    assert process.terminated is True
