"""Service helpers for the local inspection UI."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast

from agentdebug.diagnose.detect import HeuristicAnalyzer
from agentdebug.runtime import TraceStore
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    model_to_dict,
)


def _report_descriptor(report: DiagnosticReport, *, source: str) -> Dict[str, Any]:
    analyzer = str(
        report.metadata.get('analyzer')
        or report.metadata.get('mode')
        or report.metadata.get('diagnose_mode')
        or 'unknown'
    )
    return {
        'report_id': report.report_id,
        'generated_at': report.generated_at.isoformat(),
        'analyzer': analyzer,
        'finding_count': len(report.findings),
        'summary': report.summary,
        'source': source,
    }


def _resolve_trace_analysis(
    store: TraceStore,
    trajectory: AgentTrajectory,
    *,
    report_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return one selected report plus the reports available for this trace."""

    stored_reports: List[DiagnosticReport] = []
    list_reports = getattr(store, 'list_reports', None)
    if callable(list_reports):
        try:
            stored_reports = [
                report
                for report in list_reports(trajectory.trace_id)
                if isinstance(report, DiagnosticReport)
                and report.trace_id == trajectory.trace_id
            ]
        except (OSError, ValueError):
            stored_reports = []

    if report_id:
        selected = next(
            (report for report in stored_reports if report.report_id == report_id),
            None,
        )
        if selected is None:
            raise ValueError(
                f'unknown report_id {report_id!r} for trace_id {trajectory.trace_id!r}'
            )
    elif stored_reports:
        selected = stored_reports[0]
    else:
        selected = HeuristicAnalyzer().analyze(trajectory)

    source = 'stored' if selected in stored_reports else 'generated_heuristic'
    reports = [
        _report_descriptor(report, source='stored') for report in stored_reports
    ]
    if not stored_reports:
        reports.append(_report_descriptor(selected, source=source))
    return {
        'report': selected,
        'report_source': source,
        'reports': reports,
    }


def _ui_runtime_status() -> Dict[str, Any]:
    runner_url = bool(str(os.environ.get('AGENTDEBUG_RUNNER_URL') or '').strip())
    runner_command = bool(
        str(os.environ.get('AGENTDEBUG_RERUN_COMMAND') or '').strip()
    )
    policy = str(
        os.environ.get('AGENTDEBUG_UI_RERUN_POLICY') or 'from_start'
    ).strip()
    policy_valid = policy in {'from_start', 'from_event'}
    transport = 'http' if runner_url else ('process' if runner_command else None)
    return {
        'local_ui': True,
        'rerun': {
            'configured': bool(transport and policy_valid),
            'transport': transport,
            'checkpoint_policy': policy,
            'configuration_error': (
                None
                if policy_valid
                else 'AGENTDEBUG_UI_RERUN_POLICY must be from_start or from_event'
            ),
        },
    }


def _to_dict(model: Any) -> Dict[str, Any]:
    """Pydantic v1/v2 compatible serialization to dict."""
    if isinstance(model, DiagnosticReport):
        return model_to_dict(model)
    if hasattr(model, 'model_dump'):
        return cast(Dict[str, Any], model.model_dump(mode='json'))
    return cast(Dict[str, Any], json.loads(model.json()))

def _extract_chat_content(payload: Dict[str, Any]) -> str:
    choices = payload.get('choices') or []
    if not choices:
        return ''
    message = (choices[0] or {}).get('message') or {}
    content = message.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and isinstance(item.get('text'), str):
                chunks.append(item['text'])
        return '\n'.join(chunks).strip()
    return ''


def _extract_json_payload(text: str) -> Optional[Any]:
    import re

    stripped = (text or '').strip()
    candidates = [stripped] if stripped else []
    candidates.extend(
        chunk.strip()
        for chunk in re.findall(r'```(?:json)?\s*(.*?)```', text or '', flags=re.S | re.I)
        if chunk.strip()
    )
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _extract_partial_continuation_payload(text: str) -> Optional[Dict[str, Any]]:
    import re

    source = text or ''
    match = re.search(r'"continuation_events"\s*:\s*\[', source)
    if not match:
        return None

    decoder = json.JSONDecoder()
    cursor = match.end()
    events: List[Dict[str, Any]] = []

    while cursor < len(source):
        while cursor < len(source) and source[cursor] in ' \r\n\t,':
            cursor += 1
        if cursor >= len(source):
            break
        if source[cursor] == ']':
            break
        try:
            value, end = decoder.raw_decode(source, cursor)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            events.append(value)
        cursor = end

    if not events:
        return None
    return {'continuation_events': events, '_partial': True}


def _normalize_chat_endpoint(api_url: str) -> str:
    endpoint = (api_url or '').strip()
    if not endpoint:
        return endpoint
    if endpoint.endswith('/chat/completions'):
        return endpoint
    if endpoint.endswith('/v1'):
        return endpoint + '/chat/completions'
    if endpoint.endswith('/v1/'):
        return endpoint + 'chat/completions'
    return endpoint.rstrip('/') + '/v1/chat/completions'


def _request_debug_completion(
    *,
    api_url: str,
    api_key: str,
    model: str,
    prompt_text: str,
) -> Dict[str, Any]:
    import urllib.request

    endpoint = _normalize_chat_endpoint(api_url)
    request_payload = {
        'model': model,
        'messages': [
            {
                'role': 'system',
                'content': (
                    'Return JSON only. Do not include markdown fences, explanations, or chain-of-thought. '
                    'The top-level object must include continuation_events as an array. '
                    'Return 3 to 6 compact events only. Keep input and output fields brief summaries, not full transcripts.'
                ),
            },
            {'role': 'user', 'content': prompt_text},
        ],
        'temperature': 0.2,
        'max_tokens': 3200,
        'response_format': {'type': 'json_object'},
        'chat_template_kwargs': {'enable_thinking': False},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        response_text = response.read().decode('utf-8')
    value = json.loads(response_text)
    return cast(Dict[str, Any], value if isinstance(value, dict) else {})


def _extract_generated_events(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get('continuation_events'), list):
        return [item for item in payload.get('continuation_events') or [] if isinstance(item, dict)]
    trajectory_payload = payload.get('trajectory')
    if isinstance(trajectory_payload, dict) and isinstance(trajectory_payload.get('events'), list):
        return [item for item in trajectory_payload.get('events') or [] if isinstance(item, dict)]
    if isinstance(payload.get('events'), list):
        return [item for item in payload.get('events') or [] if isinstance(item, dict)]
    return []


def _normalize_generated_events(
    payload: Any,
    *,
    parent_event_id: str,
    generated_trace_id: str,
    checkpoint_step_index: Optional[int],
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    previous_event_id = parent_event_id
    base_step = checkpoint_step_index if isinstance(checkpoint_step_index, int) else 1
    for idx, event in enumerate(_extract_generated_events(payload), start=1):
        event_id = str(event.get('event_id') or f'{generated_trace_id}_evt_{idx}')
        raw_step_index = event.get('step_index')
        step_index = raw_step_index if isinstance(raw_step_index, int) else (base_step + idx - 1)
        if isinstance(checkpoint_step_index, int) and isinstance(step_index, int) and step_index < checkpoint_step_index:
            continue
        normalized.append(
            {
                'event_id': event_id,
                'trace_id': str(event.get('trace_id') or generated_trace_id),
                'parent_event_id': str(event.get('parent_event_id') or previous_event_id),
                'agent_name': str(event.get('agent_name') or 'agent'),
                'event_type': str(event.get('event_type') or 'agent.step'),
                'module': str(event.get('module') or 'module'),
                'step_index': step_index,
                'input': event.get('input'),
                'output': event.get('output'),
                'error': event.get('error'),
                'metadata': event.get('metadata') or {},
            }
        )
        previous_event_id = event_id
    return normalized


def _event_problem_text(event: Dict[str, Any]) -> str:
    payload = [
        event.get('error'),
        event.get('output'),
        event.get('input'),
        event.get('metadata'),
    ]
    return ' '.join(
        json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item or '')
        for item in payload
    ).lower()


def _event_has_problem(event: Dict[str, Any]) -> bool:
    text = _event_problem_text(event)
    return bool(
        event.get('error')
        or 'missing context' in text
        or 'premature' in text
        or 'loop' in text
        or 'handoff' in text
        or 'failure' in text
        or 'error' in text
    )


def _event_title(event: Dict[str, Any]) -> str:
    agent = str(event.get('agent_name') or 'agent')
    module = str(event.get('module') or event.get('event_type') or 'event')
    step = event.get('step_index')
    return f'{agent} / {module} / step {step if step is not None else "n/a"}'


def _build_rerun_evaluation(
    trajectory_payload: Dict[str, Any],
    report_payload: Dict[str, Any],
    *,
    event_id: str,
    generated_events: List[Dict[str, Any]],
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    events = [item for item in trajectory_payload.get('events') or [] if isinstance(item, dict)]
    findings = [item for item in report_payload.get('findings') or [] if isinstance(item, dict)]
    start_idx = next((idx for idx, event in enumerate(events) if str(event.get('event_id') or '') == event_id), -1)
    suffix = events[start_idx:] if start_idx >= 0 else []
    suffix_ids = {str(event.get('event_id') or '') for event in suffix}
    original_error_count = sum(1 for item in suffix if _event_has_problem(item))
    original_error_count += sum(
        1 for finding in findings
        if str(finding.get('event_id') or '') in suffix_ids and not any(
            str(event.get('event_id') or '') == str(finding.get('event_id') or '') and _event_has_problem(event)
            for event in suffix
        )
    )
    generated_error_count = sum(1 for item in generated_events if _event_has_problem(item))
    root_id = str(report_payload.get('root_cause_event_id') or '')
    root_in_replaced_suffix = bool(root_id and root_id in suffix_ids)
    root_like_reappeared = bool(root_id and any(str(event.get('parent_event_id') or '') == root_id for event in generated_events))
    new_error_introduced = generated_error_count > 0
    if not generated_events:
        result = 'unknown'
        reason = 'No generated rerun events were parsed, so the result cannot be evaluated.'
    elif original_error_count == 0 and generated_error_count == 0:
        result = 'unchanged'
        reason = 'The selected suffix and rerun branch both look clean under local detectors.'
    elif generated_error_count == 0 and original_error_count > 0:
        result = 'resolved'
        reason = 'The rerun branch has no local error signals while the replaced suffix had errors.'
    elif generated_error_count < original_error_count:
        result = 'improved'
        reason = 'The rerun branch reduced local error signals compared with the original suffix.'
    elif generated_error_count > original_error_count:
        result = 'worse'
        reason = 'The rerun branch introduced more local error signals than the original suffix.'
    else:
        result = 'unchanged'
        reason = 'The rerun branch has the same local error count as the original suffix.'
    score_before = 1 if original_error_count == 0 else 0
    score_after = 1 if generated_events and generated_error_count == 0 else 0
    changed_from = _event_title(suffix[0]) if suffix else 'unknown checkpoint'
    changed_to = _event_title(generated_events[0]) if generated_events else 'no generated event'
    return {
        'result': result,
        'score_before': score_before,
        'score_after': score_after,
        'error_count_before': original_error_count,
        'error_count_after': generated_error_count,
        'generated_event_count': len(generated_events),
        'root_cause_fixed': bool(root_in_replaced_suffix and generated_error_count == 0 and not root_like_reappeared),
        'new_error_introduced': new_error_introduced,
        'reason': reason,
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'method': 'local_proxy',
        'compare_summary': {
            'rerun_from_event_id': event_id,
            'rerun_from_ordinal': checkpoint.get('checkpoint_ordinal'),
            'path_changed_from': changed_from,
            'path_changed_to': changed_to,
            'original_suffix_event_count': len(suffix),
            'rerun_event_count': len(generated_events),
            'removed_error_count': max(0, original_error_count - generated_error_count),
            'added_error_count': max(0, generated_error_count - original_error_count),
        },
    }


def _decorate_debug_branch_record(
    record: Dict[str, Any],
    trajectory_payload: Dict[str, Any],
    report_payload: Dict[str, Any],
) -> Dict[str, Any]:
    decorated = dict(record)
    branch_id = str(decorated.get('branch_id') or '')
    decorated.setdefault('session_id', branch_id or ('session_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')))
    decorated.setdefault('status', 'completed' if decorated.get('generated_events') else 'created')
    if not isinstance(decorated.get('evaluation'), dict):
      decorated['evaluation'] = _build_rerun_evaluation(
          trajectory_payload,
          report_payload,
          event_id=str(decorated.get('parent_event_id') or decorated.get('event_id') or ''),
          generated_events=[
              item for item in decorated.get('generated_events') or [] if isinstance(item, dict)
          ],
          checkpoint={
              'checkpoint_ordinal': decorated.get('checkpoint_ordinal'),
              'checkpoint_step_index': decorated.get('checkpoint_step_index'),
          },
      )
    return decorated


def _compact_event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'event_id': event.get('event_id'),
        'step_index': event.get('step_index'),
        'agent_name': event.get('agent_name'),
        'event_type': event.get('event_type'),
        'module': event.get('module'),
        'input': event.get('input'),
        'output': event.get('output'),
        'error': event.get('error'),
        'metadata': event.get('metadata') or {},
    }


def _event_summary_for_prompt(event: Dict[str, Any]) -> str:
    payload = event.get('error') or event.get('output') or event.get('input') or ''
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    text = str(payload).replace('\n', ' ').strip()
    return text[:420] + ('...' if len(text) > 420 else '')


def _build_debug_continuation_context(
    trajectory: AgentTrajectory,
    report: DiagnosticReport,
    event_id: str,
    *,
    note: str = '',
    mode: str = 'debug',
    selected_event_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trajectory_payload = _to_dict(trajectory)
    report_payload = _to_dict(report)
    events = trajectory_payload.get('events') or []
    idx = next((i for i, event in enumerate(events) if str(event.get('event_id') or '') == event_id), -1)
    if idx < 0:
        if not isinstance(selected_event_override, dict):
            raise ValueError(f'unknown event_id: {event_id}')
        selected = {
            **selected_event_override,
            'event_id': str(selected_event_override.get('event_id') or event_id),
        }
        parent_event_id = str(
            selected.get('parent_event_id')
            or (selected.get('metadata') or {}).get('debug_parent_event_id')
            or ''
        )
        parent_idx = next(
            (i for i, event in enumerate(events) if str(event.get('event_id') or '') == parent_event_id),
            -1,
        )
        idx = parent_idx if parent_idx >= 0 else max(0, len(events) - 1)
        prefix_events = events[: idx + 1] + [selected]
        context_source = prefix_events
        preview_events: List[Dict[str, Any]] = []
        checkpoint_ordinal = idx + 2
    else:
        selected = events[idx]
        prefix_events = events[: idx + 1]
        context_source = events
        preview_events = events[idx + 1 : idx + 4]
        checkpoint_ordinal = idx + 1
    findings = report_payload.get('findings') or []
    selected_findings = [finding for finding in findings if finding.get('event_id') == event_id]
    root_id = report_payload.get('root_cause_event_id')
    status = 'root' if event_id == root_id else ('error' if selected_findings or selected.get('error') else 'ok')
    window_start = max(0, len(context_source) - 5)
    context_window = context_source[window_start:]
    selected_summary = _event_summary_for_prompt(selected)
    finding_summary = selected_findings[0] if selected_findings else {}
    mode_payload = finding_summary.get('failure_mode') or {}
    prompt = (
        'You are rerunning an AgentDebugX agent trajectory from a selected trace checkpoint.\n'
        f'Trace ID: {trajectory_payload.get("trace_id")}\n'
        f'Checkpoint event ordinal: {idx + 1} of {len(events)}\n'
        f'Recorded step index: {selected.get("step_index")}\n'
        f'Status: {status}\n'
        f'Agent/module/event: {selected.get("agent_name") or "agent"} / '
        f'{selected.get("module") or "module"} / {selected.get("event_type") or "event"}\n'
        f'Known failure mode: {mode_payload.get("mode_id") or mode_payload.get("name") or "n/a"}\n'
        f'Root cause event id from current analyzer: {root_id or "n/a"}\n'
        f'User note: {note or "none"}\n\n'
        'Rerun from this checkpoint. Treat previous events as fixed context, '
        'start from the selected event first, then decide the next action. Do not assume events '
        'after the checkpoint are available unless explicitly provided.\n\n'
        f'Selected event summary:\n{selected_summary or "No payload recorded."}\n'
    )
    return {
        'ok': True,
        'mode': mode,
        'trace_id': trajectory_payload.get('trace_id'),
        'report_id': report_payload.get('report_id'),
        'event_id': event_id,
        'checkpoint_ordinal': checkpoint_ordinal,
        'checkpoint_step_index': selected.get('step_index'),
        'total_events': len(events),
        'prefix_event_count': len(prefix_events),
        'remaining_event_count': max(0, len(events) - idx - 1),
        'status': status,
        'selected_event': _compact_event_payload(selected),
        'selected_findings': selected_findings,
        'root_cause_event_id': root_id,
        'root_cause_step_index': report_payload.get('root_cause_step_index'),
        'context_window': [_compact_event_payload(event) for event in context_window],
        'prefix_events': [_compact_event_payload(event) for event in prefix_events],
        'next_events_preview': [_compact_event_payload(event) for event in preview_events],
        'resume_prompt': prompt,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }



def build_overview(store: TraceStore) -> Dict[str, Any]:
    trace_ids = store.list_traces()
    event_counts: List[int] = []
    finding_counts: List[int] = []
    error_event_counts: List[int] = []
    first_error_steps: List[int] = []
    error_trace_count = 0
    error_type_counts: Dict[str, int] = {}
    error_family_counts: Dict[str, int] = {}
    framework_counts: Dict[str, int] = {}
    dataset_type_counts: Dict[str, int] = {}
    stage_counts: Dict[str, int] = {'early': 0, 'middle': 0, 'late': 0, 'none': 0}
    first_error_step_buckets: Dict[str, int] = {'1-5': 0, '6-15': 0, '16-30': 0, '31+': 0}
    root_cause_step_counts: Dict[int, int] = {}
    analyzed_trace_count = 0
    recent_traces: List[Dict[str, Any]] = []
    priority_traces: List[Dict[str, Any]] = []
    scatter_points: List[Dict[str, Any]] = []
    trace_catalog: List[Dict[str, Any]] = []
    total_findings = 0
    total_events = 0

    for trace_id in trace_ids:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            continue
        analyzed_trace_count += 1
        framework = trajectory.framework or 'unknown'
        framework_counts[framework] = framework_counts.get(framework, 0) + 1
        source_dataset = str(trajectory.metadata.get('source_dataset') or 'local/generated')
        task_type = str(
            trajectory.metadata.get('task_type')
            or trajectory.framework
            or 'unknown'
        )
        dataset_key = f'{source_dataset} / {task_type}'
        dataset_type_counts[dataset_key] = dataset_type_counts.get(dataset_key, 0) + 1
        event_count = len(trajectory.events)
        event_counts.append(event_count)
        total_events += event_count
        report = _resolve_trace_analysis(store, trajectory)['report']
        finding_count = len(report.findings)
        finding_counts.append(finding_count)
        total_findings += finding_count
        if report.findings:
            error_trace_count += 1
        error_event_ids = {finding.event_id for finding in report.findings if finding.event_id}
        error_event_count = len(error_event_ids)
        error_event_counts.append(error_event_count)
        candidate_steps = [
            finding.step_index for finding in report.findings
            if finding.step_index is not None
        ]
        if candidate_steps:
            first_error_step = min(candidate_steps)
            first_error_steps.append(first_error_step)
            root_cause_step_counts[first_error_step] = (
                root_cause_step_counts.get(first_error_step, 0) + 1
            )
            ratio = first_error_step / max(1, event_count)
            if ratio <= 0.33:
                stage_counts['early'] += 1
            elif ratio <= 0.66:
                stage_counts['middle'] += 1
            else:
                stage_counts['late'] += 1
            if first_error_step <= 5:
                first_error_step_buckets['1-5'] += 1
            elif first_error_step <= 15:
                first_error_step_buckets['6-15'] += 1
            elif first_error_step <= 30:
                first_error_step_buckets['16-30'] += 1
            else:
                first_error_step_buckets['31+'] += 1
        else:
            first_error_step = None
            stage_counts['none'] += 1
        for finding in report.findings:
            mode_id = finding.failure_mode.mode_id
            family = finding.failure_mode.family
            error_type_counts[mode_id] = error_type_counts.get(mode_id, 0) + 1
            error_family_counts[family] = error_family_counts.get(family, 0) + 1
        recent_traces.append({
            'trace_id': trace_id,
            'task_id': trajectory.task_id,
            'framework': framework,
            'event_count': event_count,
            'finding_count': finding_count,
            'first_error_step': first_error_step,
        })
        if finding_count:
            priority_score = (
                finding_count * 10
                + max(0, 20 - (first_error_step or event_count))
                + (5 if report.root_cause_event_id else 0)
            )
            priority_traces.append({
                'trace_id': trace_id,
                'framework': framework,
                'finding_count': finding_count,
                'first_error_step': first_error_step,
                'root_cause_agent': report.root_cause_agent,
                'root_cause_step_index': report.root_cause_step_index,
                'summary': report.summary,
                'score': priority_score,
            })
        scatter_points.append({
            'trace_id': trace_id,
            'framework': framework,
            'event_count': event_count,
            'finding_count': finding_count,
        })
        model_name = str(
            trajectory.metadata.get('llm_model')
            or trajectory.metadata.get('model')
            or trajectory.metadata.get('model_name')
            or trajectory.metadata.get('agent_model')
            or ''
        ).strip()
        total_duration_ms = sum(
            int(getattr(event, 'duration_ms', 0) or 0)
            for event in trajectory.events
        )
        mini_timeline = []
        max_mini_points = 48
        stride = max(1, event_count // max_mini_points)
        for idx, event in enumerate(trajectory.events):
            if idx % stride != 0 and idx != event_count - 1:
                continue
            event_id = getattr(event, 'event_id', None)
            state = 'ok'
            if event_id == report.root_cause_event_id:
                state = 'root'
            elif event_id in error_event_ids or getattr(event, 'error', None):
                state = 'error'
            mini_timeline.append({
                'event_id': event_id,
                'step_index': getattr(event, 'step_index', None),
                'state': state,
            })
        trace_catalog.append({
            'trace_id': trace_id,
            'task_id': trajectory.task_id,
            'goal': trajectory.goal,
            'framework': framework,
            'model': model_name,
            'task_type': task_type,
            'dataset_type': dataset_key,
            'event_count': event_count,
            'finding_count': finding_count,
            'error_count': error_event_count,
            'status': 'failed' if finding_count else 'passed',
            'first_error_step': first_error_step,
            'root_cause_step_index': report.root_cause_step_index,
            'root_cause_found': bool(report.root_cause_event_id),
            'duration_ms': total_duration_ms,
            'summary': report.summary,
            'mini_timeline': mini_timeline,
            'top_family': report.findings[0].failure_mode.family if report.findings else '',
            'top_error_type': report.findings[0].failure_mode.mode_id if report.findings else '',
        })

    top_error_types = sorted(
        error_type_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[:5]
    top_error_families = sorted(
        error_family_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    top_frameworks = sorted(
        framework_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[:6]
    top_dataset_types = sorted(
        dataset_type_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[:8]
    if event_counts:
        min_events = min(event_counts)
        max_events = max(event_counts)
        avg_events = round(sum(event_counts) / len(event_counts), 1)
    else:
        min_events = 0
        max_events = 0
        avg_events = 0.0
    avg_findings = round(sum(finding_counts) / len(finding_counts), 1) if finding_counts else 0.0
    avg_error_events = round(sum(error_event_counts) / len(error_event_counts), 1) if error_event_counts else 0.0
    avg_first_error_step = round(sum(first_error_steps) / len(first_error_steps), 1) if first_error_steps else None
    error_rate_pct = round((error_trace_count / analyzed_trace_count) * 100, 1) if analyzed_trace_count else 0.0

    return {
        'trace_count': len(trace_ids),
        'analyzed_trace_count': analyzed_trace_count,
        'error_trace_count': error_trace_count,
        'clean_trace_count': max(0, analyzed_trace_count - error_trace_count),
        'error_rate_pct': error_rate_pct,
        'event_total': total_events,
        'event_min': min_events,
        'event_max': max_events,
        'event_avg': avg_events,
        'finding_total': total_findings,
        'finding_avg': avg_findings,
        'error_event_avg': avg_error_events,
        'first_error_step_avg': avg_first_error_step,
        'recent_traces': recent_traces[:8],
        'top_error_types': [
            {'mode_id': mode_id, 'count': count}
            for mode_id, count in top_error_types
        ],
        'error_family_distribution': [
            {'family': family, 'count': count}
            for family, count in top_error_families
        ],
        'framework_distribution': [
            {'framework': framework, 'count': count}
            for framework, count in top_frameworks
        ],
        'failure_stage_distribution': [
            {'stage': stage, 'count': count}
            for stage, count in stage_counts.items()
        ],
        'first_error_step_histogram': [
            {'bucket': bucket, 'count': count}
            for bucket, count in first_error_step_buckets.items()
        ],
        'root_cause_step_distribution': [
            {'step': step, 'count': root_cause_step_counts.get(step, 0)}
            for step in range(1, (max(root_cause_step_counts) if root_cause_step_counts else 0) + 1)
        ],
        'top_root_cause_traces': sorted(
            priority_traces,
            key=lambda item: (-int(item['score']), str(item['trace_id'])),
        )[:5],
        'dataset_type_distribution': [
            {'dataset_type': dataset_type, 'count': count}
            for dataset_type, count in top_dataset_types
        ],
        'trace_length_findings_scatter': scatter_points,
        'trace_catalog': trace_catalog,
    }
