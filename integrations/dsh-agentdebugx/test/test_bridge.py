from __future__ import annotations

import importlib.util
import gc
import json
import tempfile
import unittest
from pathlib import Path


BRIDGE_PATH = Path(__file__).parents[1] / 'bridge' / 'agentdebug_bridge.py'
SPEC = importlib.util.spec_from_file_location('agentdebug_bridge', BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def snapshot() -> dict:
    return {
        'id': 'session/test',
        'header': {'cwd': 'C:/workspace'},
        'events': [
            {
                'type': 'turn/start',
                'seq': 0,
                'time': 1_700_000_000_000,
                'data': {'turn': 1},
            },
            {
                'type': 'user/message',
                'seq': 1,
                'time': 1_700_000_000_001,
                'data': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'Fix the failing test.'}],
                },
            },
            {
                'type': 'tool/call',
                'seq': 2,
                'time': 1_700_000_000_002,
                'data': {
                    'turn': 1,
                    'step': 1,
                    'callId': 'call-1',
                    'name': 'bash',
                    'arguments': '{"command":"pytest"}',
                },
            },
            {
                'type': 'tool/result',
                'seq': 3,
                'time': 1_700_000_000_003,
                'data': {
                    'turn': 1,
                    'step': 1,
                    'message': {
                        'source': {'kind': 'tool', 'callId': 'call-1'},
                        'content': [
                            {
                                'type': 'tool-result',
                                'toolCallId': 'call-1',
                                'content': [
                                    {'type': 'text', 'text': '1 failed'}
                                ],
                                'isError': True,
                            }
                        ],
                    },
                },
            },
            {
                'type': 'assistant/chunk',
                'seq': 4,
                'time': 1_700_000_000_004,
                'data': {'turn': 1, 'step': 1, 'chunk': {'type': 'text-delta'}},
            },
            {
                'type': 'turn/end',
                'seq': 5,
                'time': 1_700_000_000_005,
                'data': {'turn': 1, 'reason': {'kind': 'completed'}},
            },
        ],
    }


class BridgeTests(unittest.TestCase):
    def test_maps_harness_session_and_tool_parent(self) -> None:
        trajectory = bridge.session_to_trajectory(snapshot())

        self.assertEqual(trajectory.trace_id, 'dsh_session_test')
        self.assertEqual(trajectory.goal, 'Fix the failing test.')
        self.assertEqual(trajectory.framework, 'deepseek-harness')
        self.assertEqual(trajectory.metadata['skipped_assistant_chunks'], 1)
        tool_call = next(
            event for event in trajectory.events if event.event_type == 'tool.call'
        )
        failure = next(event for event in trajectory.events if event.error)
        self.assertEqual(failure.parent_event_id, tool_call.event_id)
        self.assertIn('1 failed', failure.error)

    def test_diagnose_persists_trajectory_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / 'agentdebug.sqlite')
            result = bridge.handle(
                'diagnose',
                {
                    'session': snapshot(),
                    'store': store,
                    'dashboardUrl': 'http://127.0.0.1:7777',
                },
            )

            self.assertEqual(result['summary']['traceId'], 'dsh_session_test')
            self.assertTrue(result['summary']['reportId'])
            reports = bridge._store(store).list_reports('dsh_session_test')
            self.assertEqual(len(reports), 1)
            # AgentDebugX's SQLite context managers commit but rely on object
            # finalization to close handles; collect before Windows removes
            # the temporary directory.
            gc.collect()

    def test_diagnoses_existing_trajectory_inside_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory_path = root / 'trajectory.json'
            trajectory_path.write_text(
                json.dumps(
                    bridge.model_to_dict(bridge.session_to_trajectory(snapshot()))
                ),
                encoding='utf-8',
            )
            result = bridge.handle(
                'diagnose_path',
                {
                    'path': str(trajectory_path),
                    'format': 'agenttrajectory',
                    'traceRoots': [str(root)],
                    'store': str(root / 'agentdebug.sqlite'),
                },
            )

            self.assertEqual(result['summary']['traceId'], 'dsh_session_test')
            self.assertTrue(result['summary']['reportId'])
            gc.collect()

    def test_surfaces_recorded_outcome_for_scored_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory = bridge.session_to_trajectory(snapshot())
            trajectory.metadata.update(
                {'status': 'failure', 'result_score': 0.0, 'is_infeasible': False}
            )
            trajectory_path = root / 'trajectory.json'
            trajectory_path.write_text(
                json.dumps(bridge.model_to_dict(trajectory)), encoding='utf-8'
            )

            result = bridge.handle(
                'diagnose_path',
                {
                    'path': str(trajectory_path),
                    'format': 'agenttrajectory',
                    'traceRoots': [str(root)],
                    'store': str(root / 'agentdebug.sqlite'),
                },
            )

            self.assertEqual(
                result['summary']['recordedOutcome'],
                {'status': 'failure', 'resultScore': 0.0, 'isInfeasible': False},
            )
            gc.collect()

    def test_rejects_external_trajectory_outside_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / 'allowed'
            allowed.mkdir()
            trajectory_path = root / 'trajectory.json'
            trajectory_path.write_text('{}', encoding='utf-8')

            with self.assertRaisesRegex(PermissionError, 'outside configured traceRoots'):
                bridge.handle(
                    'diagnose_path',
                    {
                        'path': str(trajectory_path),
                        'traceRoots': [str(allowed)],
                        'store': str(root / 'agentdebug.sqlite'),
                    },
                )

    def test_ingests_sessions_carrying_unencodable_characters(self) -> None:
        payload = snapshot()
        payload['events'][1]['data']['content'][0]['text'] = 'broken \ud800 text'
        payload['header']['cwd'] = 'C:/work\udfff space'

        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / 'agentdebug.sqlite')
            result = bridge.handle('ingest_snapshot', {'session': payload, 'store': store})

            self.assertEqual(result['traceId'], 'dsh_session_test')
            trajectory = bridge.session_to_trajectory(payload)
            self.assertNotIn('\ud800', bridge.model_to_dict(trajectory)['goal'])
            gc.collect()

    def test_response_encoding_failure_stays_on_the_protocol(self) -> None:
        line = bridge._encode_response({'id': '7', 'result': {'text': 'ok \ud800'}})
        response = json.loads(line)

        self.assertEqual(response['id'], '7')
        self.assertNotIn('\ud800', response['result']['text'])

    def test_json_lines_protocol_contains_errors(self) -> None:
        output = []

        class Writer:
            def write(self, value: str) -> None:
                output.append(value)

            def flush(self) -> None:
                return None

        original = bridge.sys.stdout
        try:
            bridge.sys.stdout = Writer()
            bridge.serve(
                [json.dumps({'id': '1', 'method': 'unknown', 'params': {}})]
            )
        finally:
            bridge.sys.stdout = original

        response = json.loads(''.join(output))
        self.assertEqual(response['id'], '1')
        self.assertEqual(response['error']['type'], 'ValueError')


if __name__ == '__main__':
    unittest.main()
