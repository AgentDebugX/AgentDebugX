"""Fault-isolated batch workflows for offline trajectory collections."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from agentdebug.ingest import convert_payload
from agentdebug.schema import (
    AgentTrajectory,
    DiagnosticReport,
    model_to_json,
)


@dataclass(frozen=True)
class BatchRecord:
    """One independent JSON value discovered in a batch input."""

    record_id: str
    source: str
    payload: Any
    line_number: Optional[int] = None
    parse_error: Optional[str] = None


@dataclass
class BatchItemResult:
    record_id: str
    source: str
    status: str
    line_number: Optional[int] = None
    trace_id: Optional[str] = None
    trajectory_path: Optional[str] = None
    report_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BatchSummary:
    stage: str
    input: str
    output_dir: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    items: list[BatchItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'stage': self.stage,
            'input': self.input,
            'output_dir': self.output_dir,
            'total': self.total,
            'succeeded': self.succeeded,
            'failed': self.failed,
            'items': [asdict(item) for item in self.items],
        }


DiagnoseRunner = Callable[[AgentTrajectory], DiagnosticReport]


def expand_batch_input(path: Union[str, Path]) -> list[BatchRecord]:
    """Expand a directory of JSON files or a JSONL file into records."""

    source = Path(path)
    if not source.exists():
        raise ValueError(f'batch input does not exist: {source}')
    if source.is_dir():
        files = sorted(source.rglob('*.json'))
        if not files:
            raise ValueError(f'batch input directory contains no JSON files: {source}')
        records = []
        for file_path in files:
            relative = file_path.relative_to(source).with_suffix('')
            try:
                payload = json.loads(file_path.read_text(encoding='utf-8'))
                parse_error = None
            except (OSError, json.JSONDecodeError) as exc:
                payload = None
                parse_error = f'{exc.__class__.__name__}: {exc}'
            records.append(
                BatchRecord(
                    record_id=_record_id(relative.as_posix()),
                    source=str(file_path),
                    payload=payload,
                    parse_error=parse_error,
                )
            )
        return records
    if source.suffix.lower() == '.jsonl':
        records = []
        for line_number, line in enumerate(
            source.read_text(encoding='utf-8').splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                parse_error = None
            except json.JSONDecodeError as exc:
                payload = None
                parse_error = f'{exc.__class__.__name__}: {exc}'
            records.append(
                BatchRecord(
                    record_id=f'{_record_id(source.stem)}__line_{line_number:06d}',
                    source=str(source),
                    line_number=line_number,
                    payload=payload,
                    parse_error=parse_error,
                )
            )
        if not records:
            raise ValueError(f'batch JSONL contains no JSON records: {source}')
        return records
    if source.suffix.lower() == '.json':
        return [
            BatchRecord(
                record_id=_record_id(source.stem),
                source=str(source),
                payload=json.loads(source.read_text(encoding='utf-8')),
            )
        ]
    raise ValueError('batch input must be a directory, .json file, or .jsonl file')


def run_batch_ingest(
    input_path: Union[str, Path],
    output_dir: Union[str, Path],
    *,
    format: str = 'auto',
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> BatchSummary:
    records = expand_batch_input(input_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    summary = BatchSummary(
        stage='batch.ingest',
        input=str(input_path),
        output_dir=str(destination),
        total=len(records),
    )
    for record in records:
        try:
            trajectory = _convert_record(
                record,
                format=format,
                goal=goal,
                framework=framework,
            )
            output = destination / f'{record.record_id}.trajectory.json'
            output.write_text(model_to_json(trajectory, indent=2) + '\n', encoding='utf-8')
            summary.items.append(
                _success_item(record, trajectory, trajectory_path=output)
            )
            summary.succeeded += 1
        except Exception as exc:
            summary.items.append(_failure_item(record, exc))
            summary.failed += 1
    _write_summary(summary, destination)
    return summary


def run_batch_diagnose(
    input_path: Union[str, Path],
    output_dir: Union[str, Path],
    diagnose: DiagnoseRunner,
    *,
    format: str = 'auto',
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> BatchSummary:
    records = expand_batch_input(input_path)
    destination = Path(output_dir)
    trajectory_dir = destination / 'trajectories'
    report_dir = destination / 'reports'
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = BatchSummary(
        stage='batch.diagnose',
        input=str(input_path),
        output_dir=str(destination),
        total=len(records),
    )
    for record in records:
        try:
            trajectory = _convert_record(
                record,
                format=format,
                goal=goal,
                framework=framework,
            )
            report = diagnose(trajectory)
            trajectory_path = trajectory_dir / f'{record.record_id}.trajectory.json'
            report_path = report_dir / f'{record.record_id}.report.json'
            trajectory_path.write_text(
                model_to_json(trajectory, indent=2) + '\n', encoding='utf-8'
            )
            report_path.write_text(
                model_to_json(report, indent=2) + '\n', encoding='utf-8'
            )
            summary.items.append(
                _success_item(
                    record,
                    trajectory,
                    trajectory_path=trajectory_path,
                    report_path=report_path,
                )
            )
            summary.succeeded += 1
        except Exception as exc:
            summary.items.append(_failure_item(record, exc))
            summary.failed += 1
    _write_summary(summary, destination)
    return summary


def _convert_record(
    record: BatchRecord,
    *,
    format: str,
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    if record.parse_error:
        raise ValueError(record.parse_error)
    trajectory = convert_payload(
        record.payload,
        format=format,
        goal=goal,
        framework=framework,
    )
    trajectory.metadata.update(
        {
            'batch_record_id': record.record_id,
            'batch_source': record.source,
            'batch_line_number': record.line_number,
        }
    )
    return trajectory


def _success_item(
    record: BatchRecord,
    trajectory: AgentTrajectory,
    *,
    trajectory_path: Path,
    report_path: Optional[Path] = None,
) -> BatchItemResult:
    return BatchItemResult(
        record_id=record.record_id,
        source=record.source,
        line_number=record.line_number,
        status='succeeded',
        trace_id=trajectory.trace_id,
        trajectory_path=str(trajectory_path),
        report_path=str(report_path) if report_path else None,
    )


def _failure_item(record: BatchRecord, exc: Exception) -> BatchItemResult:
    return BatchItemResult(
        record_id=record.record_id,
        source=record.source,
        line_number=record.line_number,
        status='failed',
        error=f'{exc.__class__.__name__}: {exc}',
    )


def _write_summary(summary: BatchSummary, output_dir: Path) -> None:
    (output_dir / 'batch-summary.json').write_text(
        json.dumps(summary.to_dict(), indent=2) + '\n', encoding='utf-8'
    )


def _record_id(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '__', value).strip('._-')
    return normalized or 'record'
