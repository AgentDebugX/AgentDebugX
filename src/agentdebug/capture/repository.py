"""Atomic SQLite persistence for captured trajectories and receipts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentdebug.capture.contracts import (
    CaptureReceipt,
    CaptureRepositoryStatus,
    CaptureRequest,
    CaptureSession,
    TranscriptSnapshot,
)
from agentdebug.runtime.storage import SQLiteTraceStore, _upsert_trajectory
from agentdebug.schema import AgentTrajectory, utc_now


class CaptureRepository:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 1000,
        initialize: bool = True,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        if initialize:
            SQLiteTraceStore(str(self.path))
            self._init_db()

    def begin_receipt(self, request: CaptureRequest) -> CaptureReceipt:
        notification = request.notification
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO capture_receipts(
                    receipt_id, host, session_id, project_id, transcript_path,
                    cwd, native_event_name,
                    logical_boundary_kind, native_event_id, observed_at,
                    status, task_json, session_end_reason, native_payload_json,
                    source_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    request.receipt_id,
                    notification.host,
                    notification.session_id,
                    request.project_id,
                    str(notification.transcript_path),
                    str(notification.cwd),
                    notification.event_name,
                    request.logical_boundary_kind,
                    notification.native_event_id,
                    notification.observed_at.isoformat(),
                    json.dumps(notification.task, sort_keys=True)
                    if notification.task is not None
                    else None,
                    notification.session_end_reason,
                    json.dumps(notification.native_payload, sort_keys=True),
                    request.source_version,
                ),
            )
            row = conn.execute(
                'SELECT * FROM capture_receipts WHERE receipt_id = ?',
                (request.receipt_id,),
            ).fetchone()
        assert row is not None
        return self._receipt_from_row(row)

    def load_receipt(self, receipt_id: str) -> Optional[CaptureReceipt]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM capture_receipts WHERE receipt_id = ?',
                (receipt_id,),
            ).fetchone()
        return None if row is None else self._receipt_from_row(row)

    def load_boundary_receipt(
        self, host: str, session_id: str, boundary_id: str
    ) -> Optional[CaptureReceipt]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM capture_receipts
                WHERE host = ? AND session_id = ? AND boundary_id = ?
                """,
                (host, session_id, boundary_id),
            ).fetchone()
        return None if row is None else self._receipt_from_row(row)

    def load_session(self, host: str, session_id: str) -> Optional[CaptureSession]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM capture_sessions WHERE host = ? AND session_id = ?',
                (host, session_id),
            ).fetchone()
        return None if row is None else self._session_from_row(row)

    def commit_capture(
        self,
        receipt_id: str,
        trajectory: AgentTrajectory,
        snapshot: TranscriptSnapshot,
        result_metadata: Dict[str, Any],
    ) -> None:
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            receipt = conn.execute(
                'SELECT * FROM capture_receipts WHERE receipt_id = ?',
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                raise ValueError(f'unknown capture receipt: {receipt_id}')
            _upsert_trajectory(conn, trajectory, updated_at=now)
            last_event_id = trajectory.events[-1].event_id if trajectory.events else None
            ended_at = now if receipt['native_event_name'] == 'SessionEnd' else None
            conn.execute(
                """
                INSERT INTO capture_sessions(
                    host, session_id, project_id, trace_id, transcript_path,
                    transcript_size, transcript_sha256, last_boundary_id,
                    last_event_id, event_count, status, adapter_version,
                    updated_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host, session_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    trace_id=excluded.trace_id,
                    transcript_path=excluded.transcript_path,
                    transcript_size=excluded.transcript_size,
                    transcript_sha256=excluded.transcript_sha256,
                    last_boundary_id=excluded.last_boundary_id,
                    last_event_id=excluded.last_event_id,
                    event_count=excluded.event_count,
                    status=excluded.status,
                    adapter_version=excluded.adapter_version,
                    updated_at=excluded.updated_at,
                    ended_at=excluded.ended_at
                """,
                (
                    receipt['host'],
                    receipt['session_id'],
                    result_metadata['project_id'],
                    trajectory.trace_id,
                    str(snapshot.path),
                    snapshot.complete_size,
                    snapshot.content_sha256,
                    result_metadata.get('boundary_id'),
                    last_event_id,
                    len(trajectory.events),
                    'ended' if ended_at else 'active',
                    int(result_metadata.get('adapter_version', 1)),
                    now,
                    ended_at,
                ),
            )
            conn.execute(
                """
                UPDATE capture_receipts
                SET boundary_id = ?, transcript_size = ?, status = 'committed',
                    warning_json = ?, error = NULL, duration_ms = ?
                WHERE receipt_id = ?
                """,
                (
                    result_metadata.get('boundary_id'),
                    snapshot.complete_size,
                    json.dumps(result_metadata.get('warnings', [])),
                    result_metadata.get('duration_ms'),
                    receipt_id,
                ),
            )

    def commit_no_op(
        self,
        receipt_id: str,
        snapshot: TranscriptSnapshot,
        result_metadata: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            receipt = conn.execute(
                'SELECT * FROM capture_receipts WHERE receipt_id = ?',
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                raise ValueError(f'unknown capture receipt: {receipt_id}')
            conn.execute(
                """
                UPDATE capture_receipts
                SET boundary_id = ?, transcript_size = ?, status = 'no_op',
                    warning_json = ?, duration_ms = ?
                WHERE receipt_id = ?
                """,
                (
                    result_metadata.get('boundary_id'),
                    snapshot.complete_size,
                    json.dumps(result_metadata.get('warnings', [])),
                    result_metadata.get('duration_ms'),
                    receipt_id,
                ),
            )
            if receipt['native_event_name'] == 'SessionEnd':
                now = utc_now().isoformat()
                conn.execute(
                    """
                    UPDATE capture_sessions
                    SET transcript_path = ?, transcript_size = ?,
                        transcript_sha256 = ?, last_boundary_id = ?,
                        status = 'ended', updated_at = ?, ended_at = ?
                    WHERE host = ? AND session_id = ?
                    """,
                    (
                        str(snapshot.path),
                        snapshot.complete_size,
                        snapshot.content_sha256,
                        result_metadata.get('boundary_id'),
                        now,
                        now,
                        receipt['host'],
                        receipt['session_id'],
                    ),
                )

    def mark_failed(self, receipt_id: str, error: str, duration_ms: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE capture_receipts
                SET status = 'failed', error = ?, duration_ms = ?
                WHERE receipt_id = ?
                """,
                (error, duration_ms, receipt_id),
            )

    def list_replayable(self, project_id: str) -> List[CaptureReceipt]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM capture_receipts
                WHERE status IN ('pending', 'failed') AND project_id = ?
                ORDER BY observed_at
                """,
                (project_id,),
            ).fetchall()
        return [self._receipt_from_row(row) for row in rows]

    def mark_reconciled(self, receipt_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE capture_receipts
                SET status = 'no_op', error = NULL,
                    warning_json = '["reconciled by a later delivery"]'
                WHERE receipt_id = ? AND status IN ('pending', 'failed')
                """,
                (receipt_id,),
            )

    def reconcile_prior_receipts(
        self,
        project_id: str,
        host: str,
        session_id: str,
        *,
        current_receipt_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE capture_receipts
                SET status = 'no_op', error = NULL,
                    warning_json = '["reconciled by a later cumulative snapshot"]'
                WHERE project_id = ? AND host = ? AND session_id = ?
                  AND receipt_id != ? AND status IN ('pending', 'failed')
                """,
                (project_id, host, session_id, current_receipt_id),
            )

    def status(self, project_id: str) -> CaptureRepositoryStatus:
        with self._connect() as conn:
            session_rows = conn.execute(
                'SELECT * FROM capture_sessions WHERE project_id = ? ORDER BY updated_at DESC',
                (project_id,),
            ).fetchall()
            counts = {
                str(row['status']): int(row['count'])
                for row in conn.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM capture_receipts
                    WHERE project_id = ?
                    GROUP BY status
                    """,
                    (project_id,),
                ).fetchall()
            }
        return CaptureRepositoryStatus(
            project_id=project_id,
            sessions=[self._session_from_row(row) for row in session_rows],
            pending_receipts=counts.get('pending', 0),
            failed_receipts=counts.get('failed', 0),
            committed_receipts=counts.get('committed', 0),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f'PRAGMA busy_timeout = {self.busy_timeout_ms}')
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS capture_sessions (
                    host TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    transcript_size INTEGER NOT NULL DEFAULT 0,
                    transcript_sha256 TEXT,
                    last_boundary_id TEXT,
                    last_event_id TEXT,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    adapter_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    ended_at TEXT,
                    PRIMARY KEY(host, session_id)
                );
                CREATE TABLE IF NOT EXISTS capture_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    native_event_name TEXT NOT NULL,
                    logical_boundary_kind TEXT NOT NULL,
                    boundary_id TEXT,
                    native_event_id TEXT,
                    observed_at TEXT NOT NULL,
                    transcript_size INTEGER,
                    status TEXT NOT NULL,
                    task_json TEXT,
                    session_end_reason TEXT,
                    warning_json TEXT,
                    error TEXT,
                    duration_ms REAL,
                    native_payload_json TEXT,
                    source_version TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS capture_boundary_once
                ON capture_receipts(host, session_id, boundary_id)
                WHERE boundary_id IS NOT NULL;
                """
            )

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> CaptureReceipt:
        return CaptureReceipt(
            receipt_id=row['receipt_id'],
            host=row['host'],
            session_id=row['session_id'],
            project_id=row['project_id'],
            transcript_path=row['transcript_path'],
            cwd=row['cwd'],
            native_event_name=row['native_event_name'],
            logical_boundary_kind=row['logical_boundary_kind'],
            boundary_id=row['boundary_id'],
            native_event_id=row['native_event_id'],
            observed_at=row['observed_at'],
            transcript_size=row['transcript_size'],
            status=row['status'],
            task=json.loads(row['task_json']) if row['task_json'] else None,
            session_end_reason=row['session_end_reason'],
            warnings=json.loads(row['warning_json']) if row['warning_json'] else [],
            error=row['error'],
            duration_ms=row['duration_ms'],
            native_payload=(
                json.loads(row['native_payload_json'])
                if row['native_payload_json']
                else {}
            ),
            source_version=row['source_version'],
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> CaptureSession:
        return CaptureSession(**dict(row))
