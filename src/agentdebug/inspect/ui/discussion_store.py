"""SQLite persistence for pinned AgentDebugX discussion sessions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence
from uuid import uuid4

from agentdebug.schema import (
    AgentTrajectory,
    DiagnosticReport,
    model_to_dict,
    report_from_json,
    trajectory_from_json,
)

JsonObject = Dict[str, Any]
_VALID_ROLES = {'user', 'assistant', 'system', 'tool'}
_VALID_STATUSES = {'active', 'closed', 'archived'}
_SENSITIVE_KEYS = {
    'api_key',
    'apikey',
    'authorization',
    'credential',
    'credentials',
    'hidden_reasoning',
    'raw',
    'raw_provider_response',
    'raw_response',
    'reasoning_content',
    'secret',
    'token',
}
_SECRET_PATTERNS = (
    re.compile(r'\bsk-[A-Za-z0-9_-]{8,}\b'),
    re.compile(
        r'(?i)\b(api[_ -]?key|authorization|password|secret)\b'
        r'(\s*[:=]\s*|\s+)(?:bearer\s+)?[^\s,;]+'
    ),
    re.compile(r'(?i)\bbearer\s+[^\s,;]+'),
)


class DiscussionStoreError(Exception):
    """Base persistence error with a stable route-facing code."""

    code = 'discussion_store_error'
    default_message = 'The discussion data could not be stored.'

    def __init__(self, message: Optional[str] = None) -> None:
        self.public_message = message or self.default_message
        super().__init__(self.public_message)


class DiscussionNotFoundError(DiscussionStoreError):
    code = 'discussion_not_found'
    default_message = 'The requested discussion session does not exist.'


class DiscussionVersionConflictError(DiscussionStoreError):
    code = 'discussion_version_conflict'
    default_message = 'The discussion session was modified by another request.'


class DiscussionStoreValidationError(DiscussionStoreError):
    code = 'invalid_discussion_data'
    default_message = 'The discussion data is invalid.'


@dataclass(frozen=True)
class StoredDiscussionSession:
    session_id: str
    trace_id: str
    report_id: str
    trace_snapshot: JsonObject
    report_snapshot: JsonObject
    snapshot_digest: str
    trace_digest: str
    report_digest: str
    model: str
    status: str
    version: int
    created_at: str
    updated_at: str

    @property
    def trajectory(self) -> AgentTrajectory:
        return trajectory_from_json(json.dumps(self.trace_snapshot))

    @property
    def report(self) -> DiagnosticReport:
        return report_from_json(json.dumps(self.report_snapshot))

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class StoredDiscussionMessage:
    message_id: int
    session_id: str
    sequence: int
    role: str
    content: str
    citations: List[JsonObject] = field(default_factory=list)
    proposal: Optional[JsonObject] = None
    usage: JsonObject = field(default_factory=dict)
    client_message_id: Optional[str] = None
    created_at: str = ''

    def to_dict(self) -> JsonObject:
        return asdict(self)


class SQLiteDiscussionStore:
    """Concurrent-safe local store for discussion sessions and messages."""

    def __init__(
        self,
        path: str = '.agentdebug/discussions.sqlite',
        *,
        timeout: float = 10.0,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._init_db()

    def create_session(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
        *,
        model: str,
        session_id: Optional[str] = None,
        status: str = 'active',
    ) -> StoredDiscussionSession:
        """Create and pin sanitized trajectory/report snapshots."""

        if trajectory.trace_id != report.trace_id:
            raise DiscussionStoreValidationError(
                'The trajectory and report must refer to the same trace.'
            )
        _validate_status(status)
        clean_model = _sanitize_text(str(model))
        if not clean_model:
            raise DiscussionStoreValidationError('model is required.')
        clean_trace = _sanitize_json(model_to_dict(trajectory))
        clean_report = _sanitize_json(model_to_dict(report))
        if not isinstance(clean_trace, dict) or not isinstance(clean_report, dict):
            raise DiscussionStoreValidationError()
        digest = _snapshot_digest(clean_trace, clean_report)
        identifier = str(session_id or f'disc_{uuid4().hex}')
        if not identifier.strip():
            raise DiscussionStoreValidationError('session_id cannot be empty.')
        now = _utc_now()
        try:
            with self._transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO discussion_sessions(
                        session_id, trace_id, report_id, trace_snapshot_json,
                        report_snapshot_json, snapshot_digest, trace_digest,
                        report_digest, model, status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        identifier,
                        trajectory.trace_id,
                        report.report_id,
                        _json_dump(clean_trace),
                        _json_dump(clean_report),
                        digest,
                        _digest_json(clean_trace),
                        _digest_json(clean_report),
                        clean_model,
                        status,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DiscussionStoreValidationError(
                'A discussion session with that ID already exists.'
            ) from exc
        return self._require_session(identifier)

    def get_session(
        self,
        session_id: str,
    ) -> Optional[StoredDiscussionSession]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def list_sessions(
        self,
        *,
        trace_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[StoredDiscussionSession]:
        query = 'SELECT * FROM discussion_sessions'
        clauses: List[str] = []
        params: List[str] = []
        if trace_id is not None:
            clauses.append('trace_id = ?')
            params.append(trace_id)
        if status is not None:
            _validate_status(status)
            clauses.append('status = ?')
            params.append(status)
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY updated_at DESC, session_id'
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [_session_from_row(row) for row in rows]

    def update_session(
        self,
        session_id: str,
        *,
        expected_version: int,
        status: Optional[str] = None,
        model: Optional[str] = None,
    ) -> StoredDiscussionSession:
        """Update mutable session metadata with optimistic concurrency."""

        if status is None and model is None:
            raise DiscussionStoreValidationError('No session changes were supplied.')
        if status is not None:
            _validate_status(status)
        clean_model = _sanitize_text(str(model)) if model is not None else None
        if model is not None and not clean_model:
            raise DiscussionStoreValidationError('model cannot be empty.')
        with self._transaction() as conn:
            row = conn.execute(
                'SELECT version FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            self._check_version(row, expected_version)
            result = conn.execute(
                """
                UPDATE discussion_sessions
                SET status = COALESCE(?, status),
                    model = COALESCE(?, model),
                    version = version + 1,
                    updated_at = ?
                WHERE session_id = ? AND version = ?
                """,
                (status, clean_model, _utc_now(), session_id, expected_version),
            )
            if result.rowcount != 1:
                raise DiscussionVersionConflictError()
        return self._require_session(session_id)

    def delete_session(
        self,
        session_id: str,
        *,
        expected_version: Optional[int] = None,
    ) -> None:
        """Delete a session and its messages atomically."""

        with self._transaction() as conn:
            row = conn.execute(
                'SELECT version FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            if row is None:
                raise DiscussionNotFoundError()
            if expected_version is not None and int(row['version']) != expected_version:
                raise DiscussionVersionConflictError()
            conn.execute(
                'DELETE FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            )

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        expected_version: int,
        citations: Optional[Sequence[Any]] = None,
        proposal: Optional[Mapping[str, Any]] = None,
        usage: Optional[Mapping[str, Any]] = None,
        client_message_id: Optional[str] = None,
    ) -> StoredDiscussionMessage:
        """Append one message and advance the session version atomically.

        A repeated non-empty ``client_message_id`` returns the original message
        without incrementing the version, even if the retry carries the stale
        expected version from its first attempt.
        """

        if role not in _VALID_ROLES:
            raise DiscussionStoreValidationError('Unsupported message role.')
        clean_content = _sanitize_text(str(content))
        if not clean_content:
            raise DiscussionStoreValidationError('Message content cannot be empty.')
        clean_citations = _sanitize_citations(citations or [])
        clean_proposal = _sanitize_json(dict(proposal)) if proposal is not None else None
        clean_usage = _sanitize_usage(usage or {})
        clean_client_id = (
            _sanitize_text(str(client_message_id)) if client_message_id else None
        )
        with self._transaction() as conn:
            session_row = conn.execute(
                'SELECT version FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise DiscussionNotFoundError()

            if clean_client_id is not None:
                existing = conn.execute(
                    """
                    SELECT * FROM discussion_messages
                    WHERE session_id = ? AND client_message_id = ?
                    """,
                    (session_id, clean_client_id),
                ).fetchone()
                if existing is not None:
                    return _message_from_row(existing)

            self._check_version(session_row, expected_version)
            sequence_row = conn.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM discussion_messages WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            sequence = int(sequence_row['next_sequence'])
            now = _utc_now()
            cursor = conn.execute(
                """
                INSERT INTO discussion_messages(
                    session_id, sequence, role, content, citations_json,
                    proposal_json, usage_json, client_message_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sequence,
                    role,
                    clean_content,
                    _json_dump(clean_citations),
                    _json_dump(clean_proposal) if clean_proposal is not None else None,
                    _json_dump(clean_usage),
                    clean_client_id,
                    now,
                ),
            )
            updated = conn.execute(
                """
                UPDATE discussion_sessions
                SET version = version + 1, updated_at = ?
                WHERE session_id = ? AND version = ?
                """,
                (now, session_id, expected_version),
            )
            if updated.rowcount != 1:
                raise DiscussionVersionConflictError()
            message_id = int(cursor.lastrowid)
            row = conn.execute(
                'SELECT * FROM discussion_messages WHERE message_id = ?',
                (message_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the transaction
                raise DiscussionStoreError()
            return _message_from_row(row)

    # Familiar alias for callers that phrase persistence as CRUD.
    create_message = append_message

    def get_message(
        self,
        session_id: str,
        sequence: int,
    ) -> Optional[StoredDiscussionMessage]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM discussion_messages
                WHERE session_id = ? AND sequence = ?
                """,
                (session_id, sequence),
            ).fetchone()
        return _message_from_row(row) if row is not None else None

    def update_message(
        self,
        session_id: str,
        sequence: int,
        *,
        expected_version: int,
        content: Optional[str] = None,
        citations: Optional[Sequence[Any]] = None,
        proposal: Optional[Mapping[str, Any]] = None,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> StoredDiscussionMessage:
        """Update a message and advance its session version atomically."""

        if all(item is None for item in (content, citations, proposal, usage)):
            raise DiscussionStoreValidationError('No message changes were supplied.')
        clean_content = _sanitize_text(str(content)) if content is not None else None
        if content is not None and not clean_content:
            raise DiscussionStoreValidationError('Message content cannot be empty.')
        clean_citations = _sanitize_citations(citations) if citations is not None else None
        clean_proposal = _sanitize_json(dict(proposal)) if proposal is not None else None
        clean_usage = _sanitize_usage(usage) if usage is not None else None
        with self._transaction() as conn:
            session_row = conn.execute(
                'SELECT version FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            self._check_version(session_row, expected_version)
            message_row = conn.execute(
                """
                SELECT message_id FROM discussion_messages
                WHERE session_id = ? AND sequence = ?
                """,
                (session_id, sequence),
            ).fetchone()
            if message_row is None:
                raise DiscussionNotFoundError(
                    'The requested discussion message does not exist.'
                )
            updated = conn.execute(
                """
                UPDATE discussion_messages
                SET content = COALESCE(?, content),
                    citations_json = COALESCE(?, citations_json),
                    proposal_json = COALESCE(?, proposal_json),
                    usage_json = COALESCE(?, usage_json)
                WHERE session_id = ? AND sequence = ?
                """,
                (
                    clean_content,
                    _json_dump(clean_citations) if clean_citations is not None else None,
                    _json_dump(clean_proposal) if clean_proposal is not None else None,
                    _json_dump(clean_usage) if clean_usage is not None else None,
                    session_id,
                    sequence,
                ),
            )
            if updated.rowcount != 1:  # pragma: no cover - selected in transaction
                raise DiscussionNotFoundError()
            self._advance_version(conn, session_id, expected_version)
            row = conn.execute(
                """
                SELECT * FROM discussion_messages
                WHERE session_id = ? AND sequence = ?
                """,
                (session_id, sequence),
            ).fetchone()
            if row is None:  # pragma: no cover
                raise DiscussionNotFoundError()
            return _message_from_row(row)

    def delete_message(
        self,
        session_id: str,
        sequence: int,
        *,
        expected_version: int,
    ) -> None:
        """Delete a message without renumbering later stable sequences."""

        with self._transaction() as conn:
            session_row = conn.execute(
                'SELECT version FROM discussion_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            self._check_version(session_row, expected_version)
            deleted = conn.execute(
                """
                DELETE FROM discussion_messages
                WHERE session_id = ? AND sequence = ?
                """,
                (session_id, sequence),
            )
            if deleted.rowcount != 1:
                raise DiscussionNotFoundError(
                    'The requested discussion message does not exist.'
                )
            self._advance_version(conn, session_id, expected_version)

    def list_messages(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> List[StoredDiscussionMessage]:
        if after_sequence < 0 or limit < 1 or limit > 1000:
            raise DiscussionStoreValidationError('Invalid message list bounds.')
        if self.get_session(session_id) is None:
            raise DiscussionNotFoundError()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM discussion_messages
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (session_id, after_sequence, limit),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def _require_session(self, session_id: str) -> StoredDiscussionSession:
        session = self.get_session(session_id)
        if session is None:  # pragma: no cover - only possible after external deletion
            raise DiscussionNotFoundError()
        return session

    @staticmethod
    def _check_version(
        row: Optional[sqlite3.Row],
        expected_version: int,
    ) -> None:
        if row is None:
            raise DiscussionNotFoundError()
        if expected_version < 0 or int(row['version']) != expected_version:
            raise DiscussionVersionConflictError()

    @staticmethod
    def _advance_version(
        conn: sqlite3.Connection,
        session_id: str,
        expected_version: int,
    ) -> None:
        updated = conn.execute(
            """
            UPDATE discussion_sessions
            SET version = version + 1, updated_at = ?
            WHERE session_id = ? AND version = ?
            """,
            (_utc_now(), session_id, expected_version),
        )
        if updated.rowcount != 1:
            raise DiscussionVersionConflictError()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            timeout=self.timeout,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute(f'PRAGMA busy_timeout = {max(1, int(self.timeout * 1000))}')
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.execute('COMMIT')
        except Exception:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_sessions (
                    session_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    trace_snapshot_json TEXT NOT NULL,
                    report_snapshot_json TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    trace_digest TEXT NOT NULL,
                    report_digest TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discussion_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    proposal_json TEXT,
                    usage_json TEXT NOT NULL,
                    client_message_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES discussion_sessions(session_id)
                        ON DELETE CASCADE,
                    UNIQUE(session_id, sequence),
                    UNIQUE(session_id, client_message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discussion_sessions_trace
                ON discussion_sessions(trace_id, updated_at)
                """
            )


def _session_from_row(row: sqlite3.Row) -> StoredDiscussionSession:
    return StoredDiscussionSession(
        session_id=str(row['session_id']),
        trace_id=str(row['trace_id']),
        report_id=str(row['report_id']),
        trace_snapshot=dict(json.loads(str(row['trace_snapshot_json']))),
        report_snapshot=dict(json.loads(str(row['report_snapshot_json']))),
        snapshot_digest=str(row['snapshot_digest']),
        trace_digest=str(row['trace_digest']),
        report_digest=str(row['report_digest']),
        model=str(row['model']),
        status=str(row['status']),
        version=int(row['version']),
        created_at=str(row['created_at']),
        updated_at=str(row['updated_at']),
    )


def _message_from_row(row: sqlite3.Row) -> StoredDiscussionMessage:
    proposal = (
        json.loads(str(row['proposal_json']))
        if row['proposal_json'] is not None
        else None
    )
    return StoredDiscussionMessage(
        message_id=int(row['message_id']),
        session_id=str(row['session_id']),
        sequence=int(row['sequence']),
        role=str(row['role']),
        content=str(row['content']),
        citations=list(json.loads(str(row['citations_json']))),
        proposal=dict(proposal) if isinstance(proposal, dict) else None,
        usage=dict(json.loads(str(row['usage_json']))),
        client_message_id=(
            str(row['client_message_id'])
            if row['client_message_id'] is not None
            else None
        ),
        created_at=str(row['created_at']),
    )


def _snapshot_digest(trace: JsonObject, report: JsonObject) -> str:
    data = _json_dump({'trajectory': trace, 'report': report}).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode('utf-8')).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: JsonObject = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                continue
            output[key_text] = _sanitize_json(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    cleaned = value
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub('[REDACTED]', cleaned)
    return cleaned


def _sanitize_citations(citations: Sequence[Any]) -> List[JsonObject]:
    output: List[JsonObject] = []
    for citation in citations:
        if isinstance(citation, str):
            event_id, quote = citation, None
        elif isinstance(citation, Mapping):
            event_id = str(citation.get('event_id') or '')
            quote = citation.get('quote')
        elif hasattr(citation, 'event_id'):
            event_id = str(citation.event_id)
            quote = getattr(citation, 'quote', None)
        else:
            raise DiscussionStoreValidationError('Invalid citation.')
        if not event_id:
            raise DiscussionStoreValidationError('Citation event_id is required.')
        item: JsonObject = {'event_id': _sanitize_text(event_id)}
        if quote is not None:
            item['quote'] = _sanitize_text(str(quote))
        output.append(item)
    return output


def _sanitize_usage(usage: Mapping[str, Any]) -> JsonObject:
    output: JsonObject = {}
    for key in (
        'prompt_tokens',
        'completion_tokens',
        'total_tokens',
        'calls',
        'cost_usd',
    ):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            output[key] = value
    return output


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise DiscussionStoreValidationError('Unsupported session status.')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    'DiscussionNotFoundError',
    'DiscussionStoreError',
    'DiscussionStoreValidationError',
    'DiscussionVersionConflictError',
    'SQLiteDiscussionStore',
    'StoredDiscussionMessage',
    'StoredDiscussionSession',
]
