"""Contracts for the TrajDebug / TRAJERRBENCH unified-JSON importer.

The load-bearing property is step alignment: TrajDebug's scorer reports accuracy
against ``messages[i].step``, so a `Blame` this library produces is only
comparable to a TrajDebug ``critical_error`` if ``AgentEvent.step_index`` lands
in the same index space. Everything else here guards against the ways that
alignment, or the format detection that feeds it, could silently break.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from agentdebug.ingest.adapters.importers import (
    ConversionError,
    convert_payload,
    detect_payload_format,
)


def _unified(
    messages: Optional[List[Dict[str, Any]]] = None,
    **metadata_overrides: Any,
) -> Dict[str, Any]:
    """Build a minimal but schema-faithful unified trajectory."""

    if messages is None:
        messages = [
            {'step': 0, 'role': 'user', 'name': 'human', 'content': 'put two soapbar in toilet'},
            {'step': 1, 'role': 'assistant', 'name': 'agent', 'content': 'go to toilet 1'},
            {'step': 2, 'role': 'user', 'name': 'env', 'content': 'You arrive at toilet 1.'},
            {'step': 3, 'role': 'assistant', 'name': 'agent', 'content': 'take soapbar 1'},
        ]
    metadata: Dict[str, Any] = {
        'dataset': 'alfworld',
        'task_id': 'GPT-4o_001_alfworld_task_001',
        'task_description': 'Your task is: put two soapbar in toilet.',
        'reward': 0,
        'annotation': {'critical_error_step': 3, 'critical_error_type': 'act.WrongTool'},
        'extra': {},
    }
    metadata.update(metadata_overrides)
    return {'messages': messages, 'metadata': metadata}


class TestDetection:
    def test_detects_unified_trajectory(self) -> None:
        assert detect_payload_format(_unified()) == 'trajdebug_unified'

    def test_does_not_hijack_plain_message_exports(self) -> None:
        """The generic `messages` importer must keep its own payloads.

        A unified trajectory also has a top-level ``messages`` list, so the
        detector checks for it first. If that check were loose it would capture
        every ordinary message export in the wild.
        """

        plain = {'messages': [{'role': 'user', 'content': 'hi'}], 'task_id': 'x'}
        assert detect_payload_format(plain) == 'messages'

    @pytest.mark.parametrize(
        'mutation',
        [
            pytest.param({'dataset': None}, id='no-dataset'),
            pytest.param({'reward': 2}, id='non-binary-reward'),
            pytest.param({'reward': None}, id='missing-reward'),
        ],
    )
    def test_falls_back_when_the_trajdebug_markers_are_absent(
        self, mutation: Dict[str, Any]
    ) -> None:
        payload = _unified()
        payload['metadata'].update(mutation)
        if mutation.get('dataset', 'keep') is None:
            del payload['metadata']['dataset']
        assert detect_payload_format(payload) == 'messages'


class TestStepAlignment:
    def test_one_event_per_message_with_matching_index(self) -> None:
        payload = _unified()
        traj = convert_payload(payload, format='auto')

        assert len(traj.events) == len(payload['messages'])
        assert [e.step_index for e in traj.events] == [0, 1, 2, 3]

    def test_rejects_a_step_that_disagrees_with_its_index(self) -> None:
        """Renumbering silently would destroy cross-system comparability.

        TrajDebug's own ``validate_unified`` enforces ``step == index``. A file
        violating it has been hand-edited, and quietly repairing it would mean a
        later benchmark comparison scored two different index spaces against
        each other without anything looking wrong.
        """

        payload = _unified()
        payload['messages'][2]['step'] = 7

        with pytest.raises(ConversionError, match='step == index'):
            convert_payload(payload, format='auto')


class TestGroundTruthCarriesThrough:
    def test_annotation_is_namespaced_on_metadata(self) -> None:
        traj = convert_payload(_unified(), format='auto')

        assert traj.metadata['trajdebug_critical_error_step'] == 3
        assert traj.metadata['trajdebug_critical_error_type'] == 'act.WrongTool'
        assert traj.metadata['trajdebug_reward'] == 0
        assert traj.metadata['trajdebug_dataset'] == 'alfworld'

    def test_successful_trajectories_carry_a_null_annotation(self) -> None:
        payload = _unified(
            reward=1,
            annotation={'critical_error_step': None, 'critical_error_type': None},
        )
        traj = convert_payload(payload, format='auto')

        assert traj.metadata['trajdebug_reward'] == 1
        assert traj.metadata['trajdebug_critical_error_step'] is None

    def test_framework_description_is_preserved_when_present(self) -> None:
        """Their per-dataset prose brief on what the roles mean in this format."""

        payload = _unified(
            extra={'agent_framework_description': 'The FIRST user message states the task.'}
        )
        traj = convert_payload(payload, format='auto')

        assert traj.metadata['agent_framework_description'].startswith('The FIRST')

    def test_framework_description_is_absent_rather_than_empty(self) -> None:
        traj = convert_payload(_unified(), format='auto')
        assert 'agent_framework_description' not in traj.metadata


class TestEventShape:
    def test_roles_map_to_event_types(self) -> None:
        payload = _unified(
            messages=[
                {'step': 0, 'role': 'system', 'name': 'sys', 'content': 'policy'},
                {'step': 1, 'role': 'user', 'name': 'human', 'content': 'task'},
                {'step': 2, 'role': 'assistant', 'name': 'agent', 'content': 'thinking'},
                {'step': 3, 'role': 'tool', 'name': 'search', 'content': 'result'},
            ]
        )
        traj = convert_payload(payload, format='auto')
        kinds = [str(getattr(e.event_type, 'value', e.event_type)) for e in traj.events]

        assert kinds == ['run.start', 'observation', 'agent.step', 'tool.result']

    def test_speaker_label_becomes_agent_name(self) -> None:
        """``name`` is the only multi-agent signal the unified format keeps."""

        payload = _unified(
            messages=[
                {'step': 0, 'role': 'user', 'name': 'human', 'content': 'go'},
                {'step': 1, 'role': 'assistant', 'name': 'Orchestrator', 'content': 'plan'},
                {'step': 2, 'role': 'assistant', 'name': 'WebSurfer', 'content': 'browse'},
            ]
        )
        traj = convert_payload(payload, format='auto')

        assert [e.agent_name for e in traj.events] == ['human', 'Orchestrator', 'WebSurfer']

    def test_falls_back_to_role_when_name_is_absent(self) -> None:
        payload = _unified(
            messages=[{'step': 0, 'role': 'assistant', 'content': 'no name field'}]
        )
        traj = convert_payload(payload, format='auto')

        assert traj.events[0].agent_name == 'assistant'

    def test_content_is_carried_verbatim(self) -> None:
        """Stage B's evidence quotes are substrings of this text.

        If ingest reformatted or truncated content, a quote produced against the
        unified file would no longer resolve against the ingested trajectory,
        and quote verification would fail for reasons unrelated to the model.
        """

        content = 'On the desk 1, you see a cd 3, a cellphone 1, and a pen 1.'
        payload = _unified(
            messages=[{'step': 0, 'role': 'user', 'name': 'env', 'content': content}]
        )
        traj = convert_payload(payload, format='auto')

        assert traj.events[0].output == content

    def test_goal_comes_from_task_description(self) -> None:
        traj = convert_payload(_unified(), format='auto')
        assert traj.goal == 'Your task is: put two soapbar in toilet.'

    def test_framework_records_the_source_dataset(self) -> None:
        traj = convert_payload(_unified(), format='auto')
        assert traj.framework == 'trajdebug/alfworld'


class TestMalformedInput:
    def test_rejects_a_non_string_content(self) -> None:
        payload = _unified(
            messages=[{'step': 0, 'role': 'user', 'name': 'h', 'content': {'not': 'a string'}}]
        )
        with pytest.raises(ConversionError, match='content must be a string'):
            convert_payload(payload, format='trajdebug_unified')

    def test_rejects_an_unknown_role(self) -> None:
        payload = _unified(
            messages=[{'step': 0, 'role': 'narrator', 'name': 'n', 'content': 'x'}]
        )
        with pytest.raises(ConversionError, match='role must be one of'):
            convert_payload(payload, format='trajdebug_unified')

    def test_rejects_an_empty_message_list(self) -> None:
        payload = _unified(messages=[])
        with pytest.raises(ConversionError, match='non-empty messages'):
            convert_payload(payload, format='trajdebug_unified')

    def test_rejects_a_missing_metadata_object(self) -> None:
        payload = {'messages': [{'step': 0, 'role': 'user', 'content': 'x'}]}
        with pytest.raises(ConversionError, match='metadata object'):
            convert_payload(payload, format='trajdebug_unified')
