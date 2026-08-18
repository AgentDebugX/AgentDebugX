from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agentdebug.inspect.ui.discussion_store import (
    DiscussionNotFoundError,
    DiscussionVersionConflictError,
    SQLiteDiscussionStore,
)
from agentdebug.schema import AgentEvent, AgentTrajectory, DiagnosticReport


def _snapshots() -> tuple[AgentTrajectory, DiagnosticReport]:
    trajectory = AgentTrajectory(
        trace_id='trace-store',
        goal='Persist a discussion.',
        metadata={'api_key': 'sk-snapshot-secret'},
        events=[
            AgentEvent(
                event_id='evt-store',
                trace_id='trace-store',
                output='finished',
            )
        ],
    )
    report = DiagnosticReport(
        report_id='report-store',
        trace_id='trace-store',
        summary='Pinned summary.',
    )
    return trajectory, report


def test_store_crud_and_report_snapshot_pinning(tmp_path) -> None:
    trajectory, report = _snapshots()
    store = SQLiteDiscussionStore(str(tmp_path / 'discussion.sqlite'))
    session = store.create_session(
        trajectory,
        report,
        model='test-model',
        session_id='session-1',
    )
    report.summary = 'Changed after create.'

    message = store.append_message(
        session.session_id,
        role='user',
        content='Explain this.',
        citations=['evt-store'],
        expected_version=0,
    )
    updated = store.update_session(
        session.session_id,
        status='closed',
        expected_version=1,
    )

    assert session.report.summary == 'Pinned summary.'
    assert message.sequence == 1
    assert store.get_message(session.session_id, 1) == message
    assert store.list_messages(session.session_id) == [message]
    assert updated.version == 2
    assert updated.status == 'closed'
    assert store.list_sessions(trace_id='trace-store') == [updated]

    store.delete_session(session.session_id, expected_version=2)
    assert store.get_session(session.session_id) is None
    with pytest.raises(DiscussionNotFoundError):
        store.list_messages(session.session_id)


def test_optimistic_version_conflict_and_idempotency(tmp_path) -> None:
    trajectory, report = _snapshots()
    store = SQLiteDiscussionStore(str(tmp_path / 'discussion.sqlite'))
    session = store.create_session(trajectory, report, model='model')
    first = store.append_message(
        session.session_id,
        role='user',
        content='First',
        expected_version=0,
        client_message_id='request-1',
    )
    retried = store.append_message(
        session.session_id,
        role='user',
        content='First retry',
        expected_version=0,
        client_message_id='request-1',
    )

    assert retried == first
    assert store.get_session(session.session_id).version == 1
    with pytest.raises(DiscussionVersionConflictError):
        store.append_message(
            session.session_id,
            role='assistant',
            content='Stale write',
            expected_version=0,
        )


def test_message_update_and_delete_use_session_version(tmp_path) -> None:
    trajectory, report = _snapshots()
    store = SQLiteDiscussionStore(str(tmp_path / 'discussion.sqlite'))
    session = store.create_session(trajectory, report, model='model')
    store.append_message(
        session.session_id,
        role='user',
        content='Original',
        expected_version=0,
    )

    updated = store.update_message(
        session.session_id,
        1,
        content='Edited',
        citations=['evt-store'],
        expected_version=1,
    )

    assert updated.content == 'Edited'
    assert updated.citations == [{'event_id': 'evt-store'}]
    assert store.get_session(session.session_id).version == 2
    store.delete_message(session.session_id, 1, expected_version=2)
    assert store.list_messages(session.session_id) == []
    assert store.get_session(session.session_id).version == 3


def test_store_never_persists_credentials_or_provider_internals(tmp_path) -> None:
    trajectory, report = _snapshots()
    path = tmp_path / 'discussion.sqlite'
    store = SQLiteDiscussionStore(str(path))
    session = store.create_session(trajectory, report, model='safe-model')
    store.append_message(
        session.session_id,
        role='assistant',
        content='Authorization: Bearer secret-message-token',
        expected_version=0,
        proposal={
            'summary': 'safe',
            'api_key': 'sk-proposal-secret',
            'raw_provider_response': {'secret': 'provider-secret'},
            'hidden_reasoning': 'private chain',
        },
        usage={
            'prompt_tokens': 2,
            'completion_tokens': 1,
            'api_key': 'sk-usage-secret',
        },
    )

    loaded = store.get_session(session.session_id)
    message = store.list_messages(session.session_id)[0]
    assert loaded is not None
    assert 'api_key' not in loaded.trace_snapshot['metadata']
    assert message.content == '[REDACTED]'
    assert message.proposal == {'summary': 'safe'}
    assert message.usage == {'prompt_tokens': 2, 'completion_tokens': 1}

    connection = sqlite3.connect(str(path))
    raw = '\n'.join(
        str(value)
        for table in ('discussion_sessions', 'discussion_messages')
        for row in connection.execute(f'SELECT * FROM {table}').fetchall()
        for value in row
    )
    connection.close()
    assert 'secret' not in raw.lower()
    assert 'private chain' not in raw


def test_concurrent_writers_are_atomic(tmp_path) -> None:
    trajectory, report = _snapshots()
    path = str(tmp_path / 'discussion.sqlite')
    store = SQLiteDiscussionStore(path)
    session = store.create_session(trajectory, report, model='model')
    barrier = Barrier(2)

    def write(content: str) -> str:
        local = SQLiteDiscussionStore(path)
        barrier.wait()
        try:
            local.append_message(
                session.session_id,
                role='user',
                content=content,
                expected_version=0,
            )
        except DiscussionVersionConflictError:
            return 'conflict'
        return 'written'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ['one', 'two']))

    assert sorted(results) == ['conflict', 'written']
    assert len(store.list_messages(session.session_id)) == 1
    assert store.get_session(session.session_id).version == 1


def test_concurrent_idempotent_retries_create_one_message(tmp_path) -> None:
    trajectory, report = _snapshots()
    path = str(tmp_path / 'discussion.sqlite')
    store = SQLiteDiscussionStore(path)
    session = store.create_session(trajectory, report, model='model')
    barrier = Barrier(2)

    def retry() -> int:
        local = SQLiteDiscussionStore(path)
        barrier.wait()
        return local.append_message(
            session.session_id,
            role='user',
            content='same request',
            expected_version=0,
            client_message_id='same-client-id',
        ).message_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        message_ids = list(pool.map(lambda _: retry(), range(2)))

    assert message_ids[0] == message_ids[1]
    assert len(store.list_messages(session.session_id)) == 1
    assert store.get_session(session.session_id).version == 1
