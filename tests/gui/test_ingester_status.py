"""Explicit failure evidence must survive OSWorld ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdebug.gui.ingester import IngestionResult
from agentdebug.ingest.adapters.osworld import convert_osworld_dir


@pytest.mark.parametrize('step_error,system_error', [
    ('Tool execution failed', None),
    ('', 'Environment disconnected'),
    ('Tool execution failed', 'Environment disconnected'),
    ('', None),
])
def test_positive_terminal_reward_does_not_hide_explicit_errors(
    tmp_path: Path, step_error: str, system_error: str | None,
) -> None:
    entries = [{
        'step_num': 1,
        'action': 'DONE',
        'reward': 1.0,
        'done': True,
        'info': {'error': step_error},
    }]
    if system_error is not None:
        entries.append({'Error': system_error})
    (tmp_path / 'traj.jsonl').write_text(
        ''.join(json.dumps(entry) + '\n' for entry in entries), encoding='utf-8',
    )
    (tmp_path / 'result.txt').write_text('1.0', encoding='utf-8')

    result = IngestionResult.from_directory(tmp_path)
    expected_error = step_error or system_error or ''
    expected_status = 'failure' if expected_error else 'success'
    assert result.status == expected_status
    assert result.error_msg == expected_error
    assert result.terminal_step == 1
    assert result.trajectory[0].reward == 1.0

    trajectory = convert_osworld_dir(tmp_path)
    assert trajectory.metadata['status'] == expected_status
    assert trajectory.metadata['result_score'] == 1.0
