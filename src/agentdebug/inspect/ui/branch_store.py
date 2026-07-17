"""JSONL stores used by the local inspection UI."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

from agentdebug.inspect.ui.services import (
    _extract_partial_continuation_payload,
    _normalize_generated_events,
)

LOG = logging.getLogger('agentdebug.ui')
CASE_DB_FILENAME = 'typical_error_cases.jsonl'
DEBUG_BRANCH_DB_FILENAME = 'debug_branches.jsonl'
_STORE_LOCK = threading.RLock()


def _case_db_path() -> Path:
    return Path.cwd() / CASE_DB_FILENAME


def _debug_branch_db_path() -> Path:
    return Path.cwd() / '.agentdebug' / DEBUG_BRANCH_DB_FILENAME


def _read_case_records() -> List[Dict[str, Any]]:
    with _STORE_LOCK:
        return _read_jsonl_records(_case_db_path(), label='case db')


def _append_case_record(record: Dict[str, Any]) -> None:
    with _STORE_LOCK:
        _append_jsonl_record(_case_db_path(), record)


def _delete_case_record(case_id: str) -> bool:
    with _STORE_LOCK:
        path = _case_db_path()
        records = _read_jsonl_records(path, label='case db')
        kept = [
            record
            for record in records
            if str(record.get('case_id') or '') != case_id
        ]
        if len(kept) == len(records):
            return False
        _write_jsonl_records(path, kept)
        return True


def _read_debug_branch_records() -> List[Dict[str, Any]]:
    with _STORE_LOCK:
        records = _read_jsonl_records(
            _debug_branch_db_path(),
            label='debug branch db',
        )
        for value in records:
            generated_events = value.get('generated_events')
            if not isinstance(generated_events, list) or not generated_events:
                payload = value.get('parsed_payload')
                if not isinstance(payload, dict):
                    payload = _extract_partial_continuation_payload(
                        str(value.get('response_text') or '')
                    )
                if isinstance(payload, dict):
                    value['parsed_payload'] = payload
                    value['generated_events'] = _normalize_generated_events(
                        payload,
                        parent_event_id=str(
                            value.get('parent_event_id')
                            or value.get('event_id')
                            or ''
                        ),
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
        return records


def _append_debug_branch_record(record: Dict[str, Any]) -> None:
    with _STORE_LOCK:
        _append_jsonl_record(_debug_branch_db_path(), record)


def _write_debug_branch_records(records: List[Dict[str, Any]]) -> None:
    with _STORE_LOCK:
        _write_jsonl_records(_debug_branch_db_path(), records)


def _delete_debug_branch_record(trace_id: str, session_id: str) -> bool:
    with _STORE_LOCK:
        path = _debug_branch_db_path()
        records = _read_jsonl_records(path, label='debug branch db')
        kept = [
            record
            for record in records
            if not (
                str(record.get('trace_id') or '') == trace_id
                and str(record.get('session_id') or record.get('branch_id') or '')
                == session_id
            )
        ]
        if len(kept) == len(records):
            return False
        _write_jsonl_records(path, kept)
        return True


def _read_jsonl_records(path: Path, *, label: str) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning('Skipping malformed %s line in %s', label, path)
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_jsonl_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')


def _write_jsonl_records(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    tmp.replace(path)
