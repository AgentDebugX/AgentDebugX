"""Persistence backends for traces and diagnostic reports."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Protocol

from agentdebug.schema.models import (
    AgentTrajectory,
    DiagnosticReport,
    EventType,
    model_to_json,
    report_from_json,
    trajectory_from_json,
    utc_now,
)


class TraceStore(Protocol):
    def save_trajectory(self, trajectory: AgentTrajectory) -> None:
        ...

    def load_trajectory(self, trace_id: str) -> Optional[AgentTrajectory]:
        ...

    def list_traces(self) -> List[str]:
        ...

    def save_report(self, report: DiagnosticReport) -> None:
        ...

    def list_reports(self, trace_id: Optional[str] = None) -> List[DiagnosticReport]:
        ...

    def load_report(
        self, trace_id: str, report_id: str
    ) -> Optional[DiagnosticReport]:
        ...


class JsonlTraceStore:
    """Append-only local store for quick adoption and reproducible examples."""

    def __init__(self, path: str = '.agentdebug/traces.jsonl') -> None:
        self.path = Path(path)
        self.reports_path = self.path.with_name(self.path.stem + '.reports.jsonl')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save_trajectory(self, trajectory: AgentTrajectory) -> None:
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(model_to_json(trajectory))
            handle.write('\n')

    def load_trajectory(self, trace_id: str) -> Optional[AgentTrajectory]:
        if not self.path.exists():
            return None
        match = None
        with self.path.open('r', encoding='utf-8') as handle:
            for idx, line in enumerate(handle):
                if not line.strip():
                    continue
                candidate = _trajectory_from_jsonl_line(line, idx)
                if candidate.trace_id == trace_id:
                    match = candidate
        return match

    def list_traces(self) -> List[str]:
        if not self.path.exists():
            return []
        trace_ids = []
        with self.path.open('r', encoding='utf-8') as handle:
            for idx, line in enumerate(handle):
                if not line.strip():
                    continue
                trace_ids.append(_trajectory_from_jsonl_line(line, idx).trace_id)
        return trace_ids

    def save_report(self, report: DiagnosticReport) -> None:
        with self.reports_path.open('a', encoding='utf-8') as handle:
            handle.write(model_to_json(report))
            handle.write('\n')

    def list_reports(self, trace_id: Optional[str] = None) -> List[DiagnosticReport]:
        if not self.reports_path.exists():
            return []
        reports: dict[str, DiagnosticReport] = {}
        with self.reports_path.open('r', encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                report = report_from_json(line)
                if trace_id is None or report.trace_id == trace_id:
                    reports[report.report_id] = report
        return sorted(reports.values(), key=lambda item: item.generated_at, reverse=True)

    def load_report(
        self, trace_id: str, report_id: str
    ) -> Optional[DiagnosticReport]:
        return next(
            (r for r in self.list_reports(trace_id) if r.report_id == report_id),
            None,
        )


def _trajectory_from_jsonl_line(line: str, index: int) -> AgentTrajectory:
    """Read either native AgentTrajectory JSONL or supported raw dataset rows."""

    payload = json.loads(line)
    if isinstance(payload, dict) and 'trace_id' in payload and 'events' in payload:
        return trajectory_from_json(line)
    if isinstance(payload, dict) and isinstance(payload.get('full_trajectory'), str):
        return _agenterrorbench_row_to_trajectory(payload, line=line, index=index)
    return trajectory_from_json(line)


def _agenterrorbench_row_to_trajectory(
    payload: dict[str, Any],
    *,
    line: str,
    index: int,
) -> AgentTrajectory:
    from agentdebug.ingest.adapters.importers import convert_payload

    raw_trace = payload.get('full_trajectory')
    try:
        native = json.loads(raw_trace) if isinstance(raw_trace, str) else raw_trace
    except json.JSONDecodeError:
        native = {'messages': [{'role': 'assistant', 'content': str(raw_trace or '')}]}
    trace_id = _stable_trace_id(payload, line=line, index=index)
    task_id = _opt_text(payload.get('trajectory_id')) or _opt_text(payload.get('task_id'))
    trajectory = convert_payload(
        native,
        format='auto',
        trace_id=trace_id,
        task_id=task_id,
        framework=_opt_text(payload.get('llm_model') or payload.get('task_type')) or 'agenterrorbench',
    )
    trajectory.metadata.update({
        'source_dataset': 'agenterrorbench',
        'source_format': 'agenterrorbench_jsonl',
        'trajectory_id': task_id,
        'task_type': _opt_text(payload.get('task_type')),
        'llm_model': _opt_text(payload.get('llm_model')),
        'critical_failure_step': payload.get('critical_failure_step'),
        'critical_failure_module': _opt_text(payload.get('critical_failure_module')),
        'failure_types': payload.get('failure_types') or [],
        'failure_reasonings': payload.get('failure_reasonings') or [],
        'failure_modules': payload.get('failure_modules') or [],
        'num_steps': payload.get('num_steps'),
    })
    _stabilize_converted_events(trajectory, payload)
    return trajectory


def _stable_trace_id(payload: dict[str, Any], *, line: str, index: int) -> str:
    raw = (
        _opt_text(payload.get('trace_id'))
        or _opt_text(payload.get('trajectory_id'))
        or _opt_text(payload.get('task_id'))
        or _opt_text(payload.get('id'))
    )
    if raw:
        return 'aeb_' + re.sub(r'[^A-Za-z0-9_]+', '_', raw).strip('_')
    digest = hashlib.sha1(line.encode('utf-8')).hexdigest()[:16]
    return f'aeb_row_{index + 1:04d}_{digest}'


def _stabilize_converted_events(
    trajectory: AgentTrajectory,
    payload: dict[str, Any],
) -> None:
    critical_step = _opt_int(payload.get('critical_failure_step'))
    critical_module = _opt_text(payload.get('critical_failure_module'))
    failure_types = ','.join(str(item) for item in payload.get('failure_types') or [] if item)
    reasonings = [str(item) for item in payload.get('failure_reasonings') or [] if item]
    error_text = (
        reasonings[0]
        if reasonings
        else f'AgentErrorBench critical failure: module={critical_module or "unknown"}; types={failure_types or "unlabeled"}'
    )
    for idx, event in enumerate(trajectory.events):
        event.trace_id = trajectory.trace_id
        event.event_id = f'{trajectory.trace_id}_evt_{idx + 1:03d}'
        event.metadata['source_dataset'] = 'agenterrorbench'
        if critical_step is not None and _event_matches_critical_step(event.step_index, critical_step):
            event.error = event.error or error_text
            event.metadata['critical_failure'] = True
            if critical_module:
                event.module = event.module or critical_module
                event.metadata['critical_failure_module'] = critical_module
            event.event_type = event.event_type or EventType.ERROR


def _event_matches_critical_step(step_index: Optional[int], critical_step: int) -> bool:
    if step_index is None:
        return False
    return step_index == critical_step or step_index == critical_step - 1


def _opt_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SQLiteTraceStore:
    """Small embedded error database for local development and CI artifacts."""

    def __init__(self, path: str = '.agentdebug/agentdebug.sqlite') -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_trajectory(self, trajectory: AgentTrajectory) -> None:
        payload = model_to_json(trajectory)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trajectories(trace_id, task_id, framework, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    framework=excluded.framework,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    trajectory.trace_id,
                    trajectory.task_id,
                    trajectory.framework,
                    utc_now().isoformat(),
                    payload,
                ),
            )

    def load_trajectory(self, trace_id: str) -> Optional[AgentTrajectory]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT payload_json FROM trajectories WHERE trace_id = ?',
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return trajectory_from_json(str(row[0]))

    def list_traces(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT trace_id FROM trajectories ORDER BY updated_at DESC'
            ).fetchall()
        return [str(row[0]) for row in rows]

    def save_report(self, report: DiagnosticReport) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO diagnostic_reports(
                    report_id, trace_id, generated_at, payload_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.trace_id,
                    report.generated_at.isoformat(),
                    model_to_json(report),
                ),
            )

    def list_reports(self, trace_id: Optional[str] = None) -> List[DiagnosticReport]:
        query = 'SELECT payload_json FROM diagnostic_reports'
        params: tuple[str, ...] = ()
        if trace_id is not None:
            query += ' WHERE trace_id = ?'
            params = (trace_id,)
        query += ' ORDER BY generated_at DESC'
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [report_from_json(str(row[0])) for row in rows]

    def load_report(
        self, trace_id: str, report_id: str
    ) -> Optional[DiagnosticReport]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT payload_json FROM diagnostic_reports '
                'WHERE trace_id = ? AND report_id = ?',
                (trace_id, report_id),
            ).fetchone()
        return None if row is None else report_from_json(str(row[0]))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trajectories (
                    trace_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    framework TEXT,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_reports (
                    report_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
