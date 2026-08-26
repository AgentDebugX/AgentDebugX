from datetime import datetime, timezone
from pathlib import Path

from agentdebug.capture.contracts import HookNotification
from agentdebug.capture.contracts import CaptureRequest
from agentdebug.capture.config import (
    CaptureConfig,
    PlatformCaptureConfig,
    write_capture_config,
)
from agentdebug.capture.identity import (
    project_id_for,
    receipt_id_for,
    trace_id_for,
)
from agentdebug.capture.snapshot import SnapshotError, read_complete_jsonl
from agentdebug.capture.repository import CaptureRepository
from agentdebug.capture.service import CaptureService
from agentdebug.integrations.capture_management import (
    disable_capture_integration,
    enable_capture_integration,
)
from agentdebug.capture.filtering import prepare_for_capture
from agentdebug.capture.hosts.claude_code import ClaudeCodeCaptureAdapter
from agentdebug.capture.hosts.codex import CodexCaptureAdapter
from agentdebug.diagnose.detect import HeuristicAnalyzer
from agentdebug.runtime import SQLiteTraceStore
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType


def test_capture_identities_are_stable_and_source_scoped(tmp_path: Path) -> None:
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=tmp_path / 'session.jsonl',
        cwd=tmp_path,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        native_payload={'stop_hook_active': False},
    )

    assert trace_id_for('claude_code', 'session-1') == trace_id_for(
        'claude_code', 'session-1'
    )
    assert project_id_for(tmp_path) == project_id_for(tmp_path)
    assert receipt_id_for(notification, 'source-v1') == receipt_id_for(
        notification.model_copy(update={'observed_at': datetime.now(timezone.utc)}),
        'source-v1',
    )
    assert receipt_id_for(notification, 'source-v1') != receipt_id_for(
        notification, 'source-v2'
    )


def test_snapshot_ignores_only_an_unterminated_tail(tmp_path: Path) -> None:
    transcript = tmp_path / 'session.jsonl'
    transcript.write_bytes(b'{"type":"user"}\n{"type":"assistant"}')

    snapshot = read_complete_jsonl(transcript)

    assert snapshot.records == [{'type': 'user'}]
    assert snapshot.complete_bytes == b'{"type":"user"}\n'
    assert snapshot.complete_size == len(snapshot.complete_bytes)
    assert snapshot.ignored_tail_bytes == len(b'{"type":"assistant"}')


def test_snapshot_rejects_malformed_committed_jsonl(tmp_path: Path) -> None:
    transcript = tmp_path / 'session.jsonl'
    transcript.write_bytes(b'{"type":"user"}\nnot-json\n')

    with __import__('pytest').raises(SnapshotError):
        read_complete_jsonl(transcript)


def test_capture_commit_is_visible_through_the_existing_trace_store(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text('{"type":"user"}\n', encoding='utf-8')
    snapshot = read_complete_jsonl(transcript)
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=transcript,
        cwd=tmp_path,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    request = CaptureRequest(
        notification=notification,
        project_id='project-1',
        trace_id='capture-trace',
        receipt_id='receipt-1',
        logical_boundary_kind='turn_completed',
        source_version='v1',
    )
    trajectory = AgentTrajectory(
        trace_id='capture-trace',
        events=[
            AgentEvent(
                event_id='event-1',
                trace_id='capture-trace',
                event_type=EventType.OBSERVATION,
                output='hello',
            )
        ],
    )
    store_path = tmp_path / 'agentdebug.sqlite'
    repository = CaptureRepository(store_path)

    repository.begin_receipt(request)
    repository.commit_capture(
        request.receipt_id,
        trajectory,
        snapshot,
        {
            'project_id': 'project-1',
            'boundary_id': 'boundary-1',
            'duration_ms': 1.0,
        },
    )

    saved = SQLiteTraceStore(str(store_path)).load_trajectory('capture-trace')
    status = repository.status('project-1')
    assert saved is not None and [event.event_id for event in saved.events] == [
        'event-1'
    ]
    assert status.committed_receipts == 1
    assert status.sessions[0].last_boundary_id == 'boundary-1'


def test_claude_capture_stabilizes_links_and_filters_only_owned_noise(
    tmp_path: Path,
) -> None:
    records = [
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'user-1',
            'message': {
                'role': 'user',
                'content': 'debug agentdebug with sk-' + 'a' * 24,
            },
        },
        {
            'type': 'assistant',
            'sessionId': 'session-1',
            'uuid': 'assistant-1',
            'parentUuid': 'user-1',
            'message': {
                'role': 'assistant',
                'id': 'message-1',
                'content': [
                    {'type': 'thinking', 'thinking': 'private chain'},
                    {'type': 'text', 'text': 'I will inspect it.'},
                    {
                        'type': 'tool_use',
                        'id': 'tool-1',
                        'name': 'shell',
                        'input': {'command': 'agentdebug status'},
                    },
                ],
            },
        },
        {
            'type': 'assistant',
            'sessionId': 'session-1',
            'uuid': 'assistant-owned',
            'parentUuid': 'result-1',
            'message': {
                'role': 'assistant',
                'content': [
                    {
                        'type': 'tool_use',
                        'id': 'owned-read',
                        'name': 'Read',
                        'input': {
                            'file_path': '/home/user/.claude/skills/agentdebug/SKILL.md'
                        },
                    }
                ],
            },
        },
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'owned-result',
            'parentUuid': 'assistant-owned',
            'message': {
                'role': 'user',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'owned-read',
                        'content': 'managed skill instructions',
                    }
                ],
            },
        },
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'result-1',
            'parentUuid': 'assistant-1',
            'message': {
                'role': 'user',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'tool-1',
                        'content': 'ready',
                    }
                ],
            },
        },
    ]
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(
        ''.join(__import__('json').dumps(record) + '\n' for record in records),
        encoding='utf-8',
    )
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=transcript,
        cwd=tmp_path,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    adapter = ClaudeCodeCaptureAdapter(transcript_root=tmp_path)
    snapshot = read_complete_jsonl(adapter.validate_transcript_path(notification))

    first = prepare_for_capture(
        adapter.normalize(notification, snapshot, 'capture-trace')
    )
    second = prepare_for_capture(
        adapter.normalize(notification, snapshot, 'capture-trace')
    )

    assert [event.event_id for event in first.trajectory.events] == [
        event.event_id for event in second.trajectory.events
    ]
    assert [event.parent_event_id for event in first.trajectory.events] == [
        event.parent_event_id for event in second.trajectory.events
    ]
    assert all(event.module != 'reasoning' for event in first.trajectory.events)
    assert any(
        event.input == {'command': 'agentdebug status'}
        for event in first.trajectory.events
    )
    assert '<redacted:openai_key>' in str(first.trajectory.events[0].output)
    assert 'managed skill instructions' not in str(first.trajectory)
    assert first.counters['integration_owned'] == 2
    tool_call = next(
        event for event in first.trajectory.events if event.event_type == 'tool.call'
    )
    tool_result = next(
        event for event in first.trajectory.events if event.event_type == 'tool.result'
    )
    assert tool_result.parent_event_id == tool_call.event_id


def test_disabled_capture_does_not_open_transcript_or_sqlite(tmp_path: Path) -> None:
    missing_transcript = tmp_path / 'missing.jsonl'
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=missing_transcript,
        cwd=tmp_path,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    result = CaptureService(
        tmp_path,
        ClaudeCodeCaptureAdapter(transcript_root=tmp_path),
    ).handle(notification)

    assert result.status == 'disabled'
    assert not missing_transcript.exists()
    assert not (tmp_path / '.agentdebug' / 'agentdebug.sqlite').exists()


def test_stop_capture_upserts_one_stable_trajectory_and_duplicate_is_no_op(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / 'session.jsonl'
    records = [
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'user-1',
            'message': {'role': 'user', 'content': 'hello'},
        },
        {
            'type': 'assistant',
            'sessionId': 'session-1',
            'uuid': 'assistant-1',
            'parentUuid': 'user-1',
            'message': {'role': 'assistant', 'content': 'hi'},
        },
    ]
    transcript.write_text(
        ''.join(__import__('json').dumps(record) + '\n' for record in records),
        encoding='utf-8',
    )
    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    write_capture_config(
        CaptureConfig(
            project_root=tmp_path,
            store_path=store_path,
            platforms={
                'claude_code': PlatformCaptureConfig(installed_hooks=['Stop'])
            },
        )
    )
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=transcript,
        cwd=tmp_path,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    service = CaptureService(
        tmp_path, ClaudeCodeCaptureAdapter(transcript_root=tmp_path)
    )

    captured = service.handle(notification)
    duplicate = service.handle(
        notification.model_copy(
            update={'observed_at': datetime(2026, 1, 2, tzinfo=timezone.utc)}
        )
    )

    trajectory = SQLiteTraceStore(str(store_path)).load_trajectory(captured.trace_id or '')
    assert captured.status == 'captured'
    assert duplicate.status == 'no_op'
    assert trajectory is not None
    assert len(trajectory.events) == 2
    assert len({event.event_id for event in trajectory.events}) == 2


def test_claude_lifecycle_reconciles_prior_content_and_records_signals(
    tmp_path: Path,
) -> None:
    import json

    transcript = tmp_path / 'session.jsonl'
    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    write_capture_config(
        CaptureConfig(
            project_root=tmp_path,
            store_path=store_path,
            platforms={
                'claude_code': PlatformCaptureConfig(
                    installed_hooks=[
                        'SessionStart',
                        'UserPromptSubmit',
                        'Stop',
                        'TaskCompleted',
                        'SessionEnd',
                    ]
                )
            },
        )
    )
    records = []

    def append(role: str, uuid: str, content: str, parent: str | None = None) -> None:
        record = {
            'type': role,
            'sessionId': 'session-1',
            'uuid': uuid,
            'message': {'role': role, 'content': content},
        }
        if parent:
            record['parentUuid'] = parent
        records.append(record)
        transcript.write_text(
            ''.join(json.dumps(item) + '\n' for item in records), encoding='utf-8'
        )

    append('user', 'user-1', 'first')
    service = CaptureService(
        tmp_path, ClaudeCodeCaptureAdapter(transcript_root=tmp_path)
    )

    def notice(event: str, **updates: object) -> HookNotification:
        values = dict(
            host='claude_code',
            event_name=event,
            session_id='session-1',
            transcript_path=transcript,
            cwd=tmp_path,
            observed_at=datetime.now(timezone.utc),
        )
        values.update(updates)
        return HookNotification(**values)

    assert service.handle(notice('SessionStart')).status == 'no_op'
    assert SQLiteTraceStore(str(store_path)).list_traces() == []

    append('assistant', 'assistant-1', 'first answer', 'user-1')
    assert service.handle(notice('Stop')).status == 'captured'

    append('user', 'user-2', 'second', 'assistant-1')
    append('assistant', 'assistant-2', 'second answer', 'user-2')
    append('user', 'user-3', 'third prompt', 'assistant-2')
    reconciled = service.handle(notice('UserPromptSubmit'))
    assert reconciled.status == 'captured'
    trajectory = SQLiteTraceStore(str(store_path)).load_trajectory(
        reconciled.trace_id or ''
    )
    assert trajectory is not None
    assert [event.output for event in trajectory.events][-1] == 'second answer'

    task_notice = notice(
        'TaskCompleted',
        native_event_id='task-1',
        task={'task_id': 'task-1', 'task_subject': 'check'},
    )
    stat = transcript.stat()
    source_version = f'{transcript.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}'
    task_receipt_id = receipt_id_for(task_notice, source_version)
    assert service.handle(task_notice).status == 'no_op'
    receipt = CaptureRepository(store_path).load_receipt(task_receipt_id)
    assert receipt is not None
    assert receipt.task == {'task_id': 'task-1', 'task_subject': 'check'}
    assert receipt.status == 'no_op'

    with transcript.open('ab') as handle:
        handle.write(b'{"type":"assistant"')
    assert service.handle(
        notice('SessionEnd', session_end_reason='other')
    ).status == 'no_op'
    session = CaptureRepository(store_path).load_session('claude_code', 'session-1')
    assert session is not None and session.status == 'ended'


def test_managed_capture_enable_disable_preserves_unrelated_hooks(
    tmp_path: Path,
) -> None:
    import json

    settings_path = tmp_path / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    unrelated = {
        'matcher': 'manual',
        'hooks': [{'type': 'command', 'command': '/usr/bin/unrelated'}],
    }
    settings_path.write_text(
        json.dumps({'theme': 'dark', 'hooks': {'Stop': [unrelated]}}) + '\n',
        encoding='utf-8',
    )

    enabled = enable_capture_integration('claude', tmp_path)
    enabled_again = enable_capture_integration('claude', tmp_path)
    installed = json.loads(settings_path.read_text(encoding='utf-8'))

    assert enabled['installed_hooks'] == enabled_again['installed_hooks']
    assert installed['theme'] == 'dark'
    assert unrelated in installed['hooks']['Stop']
    assert sum(
        'agentdebug-capture' in hook.get('statusMessage', '')
        for group in installed['hooks']['Stop']
        for hook in group.get('hooks', [])
    ) == 1

    store_path = Path(enabled['store_path'])
    SQLiteTraceStore(str(store_path)).save_trajectory(
        AgentTrajectory(trace_id='preserved')
    )
    disable_capture_integration('claude', tmp_path)
    disabled = json.loads(settings_path.read_text(encoding='utf-8'))

    assert disabled['hooks']['Stop'] == [unrelated]
    assert disabled['theme'] == 'dark'
    assert store_path.exists()


def test_capture_dispatch_is_fail_open_and_silent(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import io
    import json

    from agentdebug.cli.main import main

    enable_capture_integration('claude', tmp_path)
    monkeypatch.setattr(
        'sys.stdin',
        io.StringIO(
            json.dumps(
                {
                    'hook_event_name': 'Stop',
                    'session_id': 'session-1',
                    'transcript_path': str(tmp_path / 'missing.jsonl'),
                    'cwd': str(tmp_path),
                }
            )
        ),
    )

    result = main(
        [
            'integrations',
            'capture',
            'dispatch',
            '--platform',
            'claude',
            '--project',
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ''
    assert captured.err == ''


def test_capture_management_cli_enable_status_reconcile_disable(
    tmp_path: Path, capsys
) -> None:
    import json

    from agentdebug.cli.main import main

    base = ['--platform', 'claude', '--project', str(tmp_path), '--json']
    assert main(['integrations', 'capture', 'enable', *base]) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled['status'] == 'enabled'

    store_path = Path(enabled['store_path'])
    assert main(['integrations', 'capture', 'status', *base]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['enabled'] is True
    assert status['pending_receipts'] == 0
    assert not store_path.exists(), 'read-only status must not initialize SQLite'

    assert main(['integrations', 'capture', 'reconcile', *base]) == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled == {'status': 'reconciled', 'replayed': 0, 'failed': 0}

    assert main(['integrations', 'capture', 'disable', *base]) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled['status'] == 'disabled'


def test_capture_management_tracks_platforms_and_sessions_independently(
    tmp_path: Path, capsys,
) -> None:
    import json

    from agentdebug.cli.main import main

    legacy_path = tmp_path / '.agentdebug' / 'capture.json'
    legacy_path.parent.mkdir(parents=True)
    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    legacy_path.write_text(
        json.dumps(
            {
                'schema_version': 1,
                'enabled': True,
                'platform': 'codex',
                'project_root': str(tmp_path),
                'store_path': str(store_path),
                'installed_hooks': ['SessionStart', 'Stop', 'SessionEnd'],
            }
        ) + '\n',
        encoding='utf-8',
    )

    claude_base = ['--platform', 'claude', '--project', str(tmp_path), '--json']
    codex_base = ['--platform', 'codex', '--project', str(tmp_path), '--json']
    assert main(['integrations', 'capture', 'enable', *claude_base]) == 0
    capsys.readouterr()
    persisted = json.loads(legacy_path.read_text(encoding='utf-8'))
    assert persisted['schema_version'] == 2
    assert set(persisted['platforms']) == {'claude_code', 'codex'}

    repository = CaptureRepository(store_path)
    snapshot_path = tmp_path / 'snapshot.jsonl'
    snapshot_path.write_text('{}\n', encoding='utf-8')
    snapshot = read_complete_jsonl(snapshot_path)
    for host in ('claude_code', 'codex'):
        notification = HookNotification(
            host=host,
            event_name='Stop',
            session_id=f'{host}-session',
            transcript_path=snapshot_path,
            cwd=tmp_path,
            observed_at=datetime.now(timezone.utc),
        )
        request = CaptureRequest(
            notification=notification,
            project_id=project_id_for(tmp_path),
            trace_id=f'{host}-trace',
            receipt_id=f'{host}-receipt',
            logical_boundary_kind='turn_completed',
            source_version=f'{host}-source',
        )
        trajectory = AgentTrajectory(
            trace_id=request.trace_id,
            events=[
                AgentEvent(
                    trace_id=request.trace_id,
                    event_type=EventType.OBSERVATION,
                    output=host,
                )
            ],
        )
        repository.begin_receipt(request)
        repository.commit_capture(
            request.receipt_id,
            trajectory,
            snapshot,
            {
                'project_id': request.project_id,
                'boundary_id': f'{host}-boundary',
                'duration_ms': 1.0,
            },
        )

    assert main(['integrations', 'capture', 'status', *codex_base]) == 0
    codex_status = json.loads(capsys.readouterr().out)
    assert codex_status['enabled'] is True
    assert codex_status['installed_hooks'] == [
        'SessionStart', 'Stop', 'SessionEnd'
    ]
    assert [session['host'] for session in codex_status['sessions']] == ['codex']

    assert main(['integrations', 'capture', 'status', *claude_base]) == 0
    claude_status = json.loads(capsys.readouterr().out)
    assert claude_status['enabled'] is True
    assert claude_status['installed_hooks'] == [
        'SessionStart', 'UserPromptSubmit', 'Stop', 'TaskCompleted', 'SessionEnd'
    ]
    assert [session['host'] for session in claude_status['sessions']] == [
        'claude_code'
    ]

    assert main(['integrations', 'capture', 'disable', *claude_base]) == 0
    capsys.readouterr()
    assert main(['integrations', 'capture', 'status', *claude_base]) == 0
    assert json.loads(capsys.readouterr().out)['enabled'] is False
    assert main(['integrations', 'capture', 'status', *codex_base]) == 0
    assert json.loads(capsys.readouterr().out)['enabled'] is True


def test_codex_audited_rollout_normalizes_stably_without_private_records() -> None:
    fixture = Path(__file__).parent / 'fixtures' / 'codex' / 'rollout.jsonl'
    notification = HookNotification(
        host='codex',
        event_name='Stop',
        session_id='codex-session-1',
        transcript_path=fixture,
        cwd=fixture.parent,
        observed_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        native_event_id='turn-1',
    )
    adapter = CodexCaptureAdapter(transcript_root=fixture.parent)
    snapshot = read_complete_jsonl(adapter.validate_transcript_path(notification))

    first = prepare_for_capture(
        adapter.normalize(notification, snapshot, 'codex-trace')
    ).trajectory
    second = prepare_for_capture(
        adapter.normalize(notification, snapshot, 'codex-trace')
    ).trajectory

    assert [event.event_id for event in first.events] == [
        event.event_id for event in second.events
    ]
    assert [event.event_type for event in first.events] == [
        'observation',
        'tool.call',
        'tool.result',
        'llm.response',
    ]
    assert first.events[2].parent_event_id == first.events[1].event_id
    assert first.goal == 'inspect the project'
    assert 'AGENTS.md instructions' not in str(first)
    assert 'repeated loops' not in str(first)
    assert 'private harness instructions' not in str(first)
    assert 'private reasoning' not in str(first)
    assert 'total_tokens' not in str(first)
    report = HeuristicAnalyzer(rule_packs='core').analyze(first)
    assert all(
        finding.rule_id != 'core.planning.inefficient_plan'
        for finding in report.findings
    )


def test_codex_supported_hook_set_captures_a_rollout_end_to_end(
    tmp_path: Path, capsys,
) -> None:
    import json

    from agentdebug.cli.main import main

    fixture = Path(__file__).parent / 'fixtures' / 'codex' / 'rollout.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_bytes(fixture.read_bytes())
    enabled = enable_capture_integration('codex', tmp_path)
    assert enabled['installed_hooks'] == [
        'SessionStart',
        'UserPromptSubmit',
        'Stop',
        'SessionEnd',
    ]
    assert 'TaskCompleted' not in enabled['installed_hooks']
    adapter = CodexCaptureAdapter(transcript_root=tmp_path)
    service = CaptureService(tmp_path, adapter)

    def notice(event: str, **updates: object) -> HookNotification:
        values = dict(
            host='codex',
            event_name=event,
            session_id='codex-session-1',
            transcript_path=transcript,
            cwd=tmp_path,
            observed_at=datetime.now(timezone.utc),
        )
        values.update(updates)
        return HookNotification(**values)

    assert service.handle(notice('SessionStart')).status == 'no_op'
    captured = service.handle(notice('Stop', native_event_id='turn-1'))
    assert captured.status == 'captured'
    assert service.handle(
        notice('UserPromptSubmit', native_event_id='turn-2')
    ).status == 'no_op'
    assert service.handle(notice('SessionEnd', session_end_reason='other')).status == 'no_op'

    store = SQLiteTraceStore(enabled['store_path'])
    trajectory = store.load_trajectory(captured.trace_id or '')
    assert trajectory is not None and len(trajectory.events) == 4
    assert trajectory.metadata['codex_cli_version'] == '0.149.1'
    assert 'capture_source_version' not in trajectory.metadata
    assert store.list_reports(captured.trace_id) == []

    result = main(
        [
            'run',
            captured.trace_id or '',
            '--store-sqlite',
            enabled['store_path'],
            '--run-root',
            str(tmp_path / 'runs'),
            '--profile',
            'quick',
            '--json',
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report['trace_id'] == captured.trace_id


def test_codex_resume_keeps_one_trajectory_and_ignores_empty_launcher(
    tmp_path: Path,
) -> None:
    import json

    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    write_capture_config(
        CaptureConfig(
            project_root=tmp_path,
            store_path=store_path,
            platforms={
                'codex': PlatformCaptureConfig(
                    installed_hooks=['SessionStart', 'Stop', 'SessionEnd']
                )
            },
        )
    )
    resumed_path = tmp_path / 'resumed.jsonl'
    records = [
        {
            'type': 'session_meta',
            'payload': {'id': 'resumed-session', 'cwd': str(tmp_path)},
        },
        {
            'type': 'response_item',
            'payload': {
                'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'create hello.txt'}],
            },
        },
        {
            'type': 'response_item',
            'payload': {
                'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'created'}],
            },
        },
    ]

    def write_records(path: Path, values: list[dict[str, object]]) -> None:
        path.write_text(
            ''.join(json.dumps(value) + '\n' for value in values),
            encoding='utf-8',
        )

    def notice(event: str, session_id: str, path: Path) -> HookNotification:
        return HookNotification(
            host='codex',
            event_name=event,
            session_id=session_id,
            transcript_path=path,
            cwd=tmp_path,
            observed_at=datetime.now(timezone.utc),
        )

    write_records(resumed_path, records)
    service = CaptureService(tmp_path, CodexCaptureAdapter(transcript_root=tmp_path))
    first = service.handle(notice('Stop', 'resumed-session', resumed_path))
    assert first.status == 'captured'
    assert service.handle(
        notice('SessionEnd', 'resumed-session', resumed_path)
    ).status == 'no_op'

    records.append({'type': 'turn_context', 'payload': {'cwd': str(tmp_path)}})
    write_records(resumed_path, records)
    assert service.handle(
        notice('SessionStart', 'resumed-session', resumed_path)
    ).status == 'captured'
    active = CaptureRepository(store_path).load_session('codex', 'resumed-session')
    assert active is not None and active.status == 'active'
    assert active.ended_at is None

    records.extend(
        [
            {
                'type': 'response_item',
                'payload': {
                    'type': 'message', 'role': 'user',
                    'content': [
                        {'type': 'input_text', 'text': 'update hello_V2.txt'}
                    ],
                },
            },
            {
                'type': 'response_item',
                'payload': {
                    'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'updated'}],
                },
            },
        ]
    )
    write_records(resumed_path, records)
    assert service.handle(
        notice('Stop', 'resumed-session', resumed_path)
    ).status == 'captured'
    assert service.handle(
        notice('SessionEnd', 'resumed-session', resumed_path)
    ).status == 'no_op'

    launcher_path = tmp_path / 'launcher.jsonl'
    write_records(
        launcher_path,
        [{'type': 'session_meta', 'payload': {
            'id': 'launcher-session', 'cwd': str(tmp_path)
        }}],
    )
    launcher_notice = notice('SessionEnd', 'launcher-session', launcher_path)
    launcher_end = service.handle(launcher_notice)

    store = SQLiteTraceStore(str(store_path))
    trajectory = store.load_trajectory(first.trace_id or '')
    assert trajectory is not None
    assert [event.output for event in trajectory.events] == [
        'create hello.txt', 'created', 'update hello_V2.txt', 'updated'
    ]
    assert launcher_end.status == 'no_op'
    assert launcher_end.event_count == 0
    assert store.load_trajectory(launcher_end.trace_id or '') is None
    stat = launcher_path.stat()
    source_version = (
        f'{launcher_path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}'
    )
    launcher_receipt = CaptureRepository(store_path).load_receipt(
        receipt_id_for(launcher_notice, source_version)
    )
    assert launcher_receipt is not None
    assert launcher_receipt.status == 'no_op'


def test_failed_snapshot_does_not_advance_state_and_next_boundary_reconciles(
    tmp_path: Path,
) -> None:
    import json

    transcript = tmp_path / 'session.jsonl'
    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    records = [
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'u1',
            'message': {'role': 'user', 'content': 'one'},
        },
        {
            'type': 'assistant',
            'sessionId': 'session-1',
            'uuid': 'a1',
            'parentUuid': 'u1',
            'message': {'role': 'assistant', 'content': 'answer one'},
        },
    ]
    transcript.write_text(
        ''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8'
    )
    write_capture_config(
        CaptureConfig(
            project_root=tmp_path,
            store_path=store_path,
            platforms={
                'claude_code': PlatformCaptureConfig(installed_hooks=['Stop'])
            },
        )
    )
    service = CaptureService(
        tmp_path, ClaudeCodeCaptureAdapter(transcript_root=tmp_path)
    )

    def stop() -> HookNotification:
        return HookNotification(
            host='claude_code',
            event_name='Stop',
            session_id='session-1',
            transcript_path=transcript,
            cwd=tmp_path,
            observed_at=datetime.now(timezone.utc),
        )

    first = service.handle(stop())
    session_before = CaptureRepository(store_path).load_session(
        'claude_code', 'session-1'
    )
    with transcript.open('a', encoding='utf-8') as handle:
        handle.write('not-json\n')
    assert service.handle(stop()).status == 'failed'
    session_failed = CaptureRepository(store_path).load_session(
        'claude_code', 'session-1'
    )
    assert session_failed == session_before

    records.extend(
        [
            {
                'type': 'user',
                'sessionId': 'session-1',
                'uuid': 'u2',
                'parentUuid': 'a1',
                'message': {'role': 'user', 'content': 'two'},
            },
            {
                'type': 'assistant',
                'sessionId': 'session-1',
                'uuid': 'a2',
                'parentUuid': 'u2',
                'message': {'role': 'assistant', 'content': 'answer two'},
            },
        ]
    )
    transcript.write_text(
        ''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8'
    )
    second = service.handle(stop())
    status = CaptureRepository(store_path).status(project_id_for(tmp_path))
    trajectory = SQLiteTraceStore(str(store_path)).load_trajectory(
        first.trace_id or ''
    )

    assert second.status == 'captured'
    assert status.failed_receipts == 0
    assert trajectory is not None and len(trajectory.events) == 4


def test_concurrent_duplicate_delivery_commits_one_content_boundary(
    tmp_path: Path,
) -> None:
    import json
    from concurrent.futures import ThreadPoolExecutor

    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(
        '\n'.join(
            json.dumps(record)
            for record in (
                {
                    'type': 'user',
                    'sessionId': 'session-1',
                    'uuid': 'u1',
                    'message': {'role': 'user', 'content': 'hello'},
                },
                {
                    'type': 'assistant',
                    'sessionId': 'session-1',
                    'uuid': 'a1',
                    'parentUuid': 'u1',
                    'message': {'role': 'assistant', 'content': 'hi'},
                },
            )
        )
        + '\n',
        encoding='utf-8',
    )
    store_path = tmp_path / '.agentdebug' / 'agentdebug.sqlite'
    write_capture_config(
        CaptureConfig(
            project_root=tmp_path,
            store_path=store_path,
            platforms={
                'claude_code': PlatformCaptureConfig(installed_hooks=['Stop'])
            },
        )
    )
    notification = HookNotification(
        host='claude_code',
        event_name='Stop',
        session_id='session-1',
        transcript_path=transcript,
        cwd=tmp_path,
        observed_at=datetime.now(timezone.utc),
    )

    def deliver(_: int) -> str:
        return CaptureService(
            tmp_path, ClaudeCodeCaptureAdapter(transcript_root=tmp_path)
        ).handle(notification).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(deliver, range(8)))

    repository = CaptureRepository(store_path)
    status = repository.status(project_id_for(tmp_path))
    session = repository.load_session('claude_code', 'session-1')
    assert 'captured' in statuses
    assert status.committed_receipts == 1
    assert session is not None and session.event_count == 2
