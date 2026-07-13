"""JSONL stores used by the local inspection UI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from agentdebug.inspect.ui.services import (
    _extract_partial_continuation_payload,
    _normalize_generated_events,
)

LOG = logging.getLogger('agentdebug.ui')
CASE_DB_FILENAME = 'typical_error_cases.jsonl'
DEBUG_BRANCH_DB_FILENAME = 'debug_branches.jsonl'

def _case_db_path() -> Path:
    return Path.cwd() / CASE_DB_FILENAME


def _debug_branch_db_path() -> Path:
    return Path.cwd() / '.agentdebug' / DEBUG_BRANCH_DB_FILENAME


def _read_case_records() -> List[Dict[str, Any]]:
    path = _case_db_path()
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning('Skipping malformed case db line in %s', path)
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_case_record(record: Dict[str, Any]) -> None:
    path = _case_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')


def _delete_case_record(case_id: str) -> bool:
    path = _case_db_path()
    if not path.exists():
        return False
    records = _read_case_records()
    kept = [record for record in records if str(record.get('case_id') or '') != case_id]
    if len(kept) == len(records):
        return False
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        for record in kept:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    tmp.replace(path)
    return True


def _read_debug_branch_records() -> List[Dict[str, Any]]:
    path = _debug_branch_db_path()
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning('Skipping malformed debug branch line in %s', path)
            continue
        if isinstance(value, dict):
            generated_events = value.get('generated_events')
            if not isinstance(generated_events, list) or not generated_events:
                payload = value.get('parsed_payload')
                if not isinstance(payload, dict):
                    payload = _extract_partial_continuation_payload(str(value.get('response_text') or ''))
                if isinstance(payload, dict):
                    value['parsed_payload'] = payload
                    value['generated_events'] = _normalize_generated_events(
                        payload,
                        parent_event_id=str(value.get('parent_event_id') or value.get('event_id') or ''),
                        generated_trace_id=str(
                            value.get('generated_trace_id')
                            or value.get('trace_id')
                            or f'branch_{value.get("branch_id") or "generated"}'
                        ),
                        checkpoint_step_index=(
                            value.get('checkpoint_step_index')
                            if isinstance(value.get('checkpoint_step_index'), int)
                            else None
                        ),
                    )
            records.append(value)
    return records


def _append_debug_branch_record(record: Dict[str, Any]) -> None:
    path = _debug_branch_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')


def _write_debug_branch_records(records: List[Dict[str, Any]]) -> None:
    path = _debug_branch_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    tmp.replace(path)


