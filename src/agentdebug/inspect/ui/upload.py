"""Upload parsing and trajectory normalization for the local inspection UI."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agentdebug.ingest import convert_payload
from agentdebug.inspect.ui.llm_convert import LLMConversionError, convert_with_llm
from agentdebug.runtime.storage import trajectory_from_jsonl_record
from agentdebug.schema import AgentTrajectory


MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def split_upload_payloads(text: str) -> List[Any]:
    """Split JSON, a message array, or JSONL into trajectory-shaped values."""

    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] == '{':
        try:
            return [json.loads(stripped)]
        except json.JSONDecodeError:
            pass
    if stripped[0] == '[':
        values = json.loads(stripped)
        if not isinstance(values, list):
            raise ValueError('top-level JSON must be an object or array')
        if values and all(
            isinstance(item, dict) and ('role' in item or 'content' in item)
            for item in values
        ):
            return [values]
        return values
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def derive_upload_trace_id(payload: Any, index: int) -> str:
    if isinstance(payload, dict):
        for key in ('trace_id', 'trajectory_id', 'task_id', 'question_ID', 'id', 'run_id'):
            value = payload.get(key)
            if value:
                slug = re.sub(r'[^A-Za-z0-9_]+', '_', str(value)).strip('_')
                if slug:
                    return f'upload_{slug}'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')
    return f'upload_{stamp}_{index:03d}'


def convert_upload_payload(
    payload: Any,
    index: int,
    *,
    allow_llm: bool = True,
    base_url: str = '',
    api_key: str = '',
    model: str = '',
) -> tuple[AgentTrajectory, str]:
    """Normalize one payload and return the trajectory plus converter label."""

    deterministic_error: Optional[Exception] = None
    if isinstance(payload, dict) and (
        ('trace_id' in payload and 'events' in payload)
        or isinstance(payload.get('full_trajectory'), str)
    ):
        try:
            return trajectory_from_jsonl_record(json.dumps(payload), index), 'native'
        except Exception as exc:  # fall through to the shared adapters
            deterministic_error = exc

    trace_id = derive_upload_trace_id(payload, index)
    try:
        return convert_payload(payload, format='auto', trace_id=trace_id), 'adapter'
    except Exception as exc:
        deterministic_error = exc

    if not allow_llm:
        raise ValueError(str(deterministic_error or 'unsupported trajectory format'))
    try:
        return (
            convert_with_llm(
                payload,
                trace_id,
                base_url=base_url,
                api_key=api_key,
                model=model,
            ),
            'llm',
        )
    except LLMConversionError as exc:
        parser_error = str(deterministic_error or '')[:240]
        suffix = f' (parser: {parser_error})' if parser_error else ''
        raise ValueError(f'{exc}{suffix}') from exc


def import_upload_text(
    store: Any,
    text: str,
    *,
    allow_llm: bool = False,
    base_url: str = '',
    api_key: str = '',
    model: str = '',
) -> Dict[str, Any]:
    raw_size = len(text.encode('utf-8'))
    if raw_size > MAX_UPLOAD_BYTES:
        raise ValueError('file too large (>25 MB)')
    payloads = split_upload_payloads(text)
    if not payloads:
        raise ValueError('no trajectories found in upload')

    imported: List[str] = []
    errors: List[Dict[str, Any]] = []
    converters: Dict[str, str] = {}
    for index, payload in enumerate(payloads):
        try:
            trajectory, converter = convert_upload_payload(
                payload,
                index,
                allow_llm=allow_llm,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
        except Exception as exc:
            errors.append({'index': index, 'error': str(exc)[:500]})
            continue
        store.save_trajectory(trajectory)
        imported.append(trajectory.trace_id)
        converters[trajectory.trace_id] = converter

    if not imported:
        detail = errors[0]['error'] if len(errors) == 1 else 'could not parse any trajectory'
        raise ValueError(detail)
    return {
        'imported': imported,
        'count': len(imported),
        'errors': errors,
        'converters': converters,
        'llm_converted': [tid for tid, source in converters.items() if source == 'llm'],
    }
