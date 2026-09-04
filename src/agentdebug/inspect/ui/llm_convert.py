"""LLM-driven fallback conversion of arbitrary uploaded JSON into AgentTrajectory.

The deterministic importers in ``agentdebug.ingest.adapters.importers`` handle
the known formats. When they cannot make sense of an upload, this module asks
the server's configured LLM (``AGENTDEBUG_LLM_*`` env) to map the payload onto
the AgentTrajectory schema. If the payload does not describe an agent run at
all, the model is instructed to refuse and the upload error says so.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from agentdebug.core.llm import OpenAICompatClient, extract_json_block
from agentdebug.core.models import AgentTrajectory


def _loads_lenient(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from model output, tolerating common LLM slips.

    Falls back through: strict extract → strip code fences → greedy slice
    between the outer braces → ``json.loads(..., strict=False)`` which permits
    literal control characters (unescaped newlines/tabs) inside strings, a
    frequent cause of otherwise-valid model JSON failing to parse.
    """
    if not text:
        return None
    strict = extract_json_block(text)
    if isinstance(strict, dict):
        return strict
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
        if cleaned.endswith('```'):
            cleaned = cleaned[: -len('```')]
        cleaned = cleaned.strip()
    start = cleaned.find('{')
    if start == -1:
        return None
    body = cleaned[start:]
    end = body.rfind('}')
    if end != -1:
        try:
            parsed = json.loads(body[: end + 1], strict=False)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    # Truncated output: close any open strings/brackets and retry so a run
    # that got cut off mid-array still yields the events produced so far.
    repaired = _close_truncated_json(body)
    if repaired is not None:
        try:
            parsed = json.loads(repaired, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _close_truncated_json(text: str) -> Optional[str]:
    """Best-effort completion of a JSON object cut off mid-generation.

    Walks the text tracking string state and the bracket stack, drops any
    trailing partial token, and appends the closing brackets needed to balance.
    """
    stack = []
    in_string = False
    escape = False
    last_safe = -1  # index just after the last comma/close at depth-safe point
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack:
                stack.pop()
            last_safe = i + 1
        elif ch == ',':
            last_safe = i + 1
    if not stack:
        return None
    # Cut back to the last completed value, then close open containers.
    trimmed = text[:last_safe].rstrip().rstrip(',') if last_safe > 0 else text
    # Recompute the still-open stack for the trimmed prefix.
    stack = []
    in_string = False
    escape = False
    for ch in trimmed:
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack:
                stack.pop()
    if in_string:
        return None
    closers = ''.join(']' if b == '[' else '}' for b in reversed(stack))
    return trimmed + closers

MAX_PAYLOAD_CHARS = 60_000

EVENT_TYPES = [
    'run.start', 'run.end', 'agent.step', 'llm.call', 'llm.response',
    'tool.call', 'tool.result', 'memory.read', 'memory.write', 'reflection',
    'plan', 'handoff', 'guardrail', 'observation', 'error', 'human.feedback',
]

SCHEMA_EXAMPLE: Dict[str, Any] = {
    'trace_id': 'my_run_001',
    'goal': 'Book a refundable flight to Paris',
    'framework': 'my-agent',
    'metadata': {'any': 'extra info'},
    'events': [
        {
            'trace_id': 'my_run_001',
            'event_id': 'evt_1',
            'agent_name': 'planner',
            'event_type': 'plan',
            'step_index': 0,
            'input': 'user request...',
            'output': 'Search flights, then compare fares.',
        },
        {
            'trace_id': 'my_run_001',
            'event_id': 'evt_2',
            'agent_name': 'browser',
            'event_type': 'tool.result',
            'step_index': 1,
            'input': {'tool': 'open_url', 'url': 'https://...'},
            'output': None,
            'error': 'Timeout while loading checkout',
        },
    ],
}


def schema_payload() -> Dict[str, Any]:
    """Machine-readable upload contract served at /api/v1/schema."""
    try:
        json_schema = AgentTrajectory.model_json_schema()  # pydantic v2
    except AttributeError:  # pragma: no cover - pydantic v1
        json_schema = AgentTrajectory.schema()
    return {
        'format': 'AgentTrajectory',
        'upload_accepts': [
            'a single trajectory JSON object',
            'a JSON array of trajectory objects',
            'JSON Lines: one trajectory object per line (.jsonl)',
            'known agent-log formats auto-converted server-side: messages, '
            'conversations, event lists, OpenAI Agents spans, CrewAI events, '
            'LangGraph callbacks, WebShop logs, AgentErrorBench rows',
            'anything else JSON-shaped: an LLM converter maps it onto '
            'AgentTrajectory on a best-effort basis',
        ],
        'required_fields': {
            'trajectory': ['trace_id', 'events'],
            'event': ['trace_id (must equal the trajectory trace_id)'],
        },
        'event_types': EVENT_TYPES,
        'example': SCHEMA_EXAMPLE,
        'json_schema': json_schema,
        'limits': {'max_upload_bytes': 25 * 1024 * 1024, 'encoding': 'utf-8'},
    }


def llm_available() -> bool:
    return bool(
        os.environ.get('AGENTDEBUG_LLM_BASE_URL')
        and os.environ.get('AGENTDEBUG_LLM_API_KEY')
    )


class LLMConversionError(ValueError):
    pass


_CONVERT_INSTRUCTIONS = """You convert arbitrary AI-agent run logs into the AgentTrajectory JSON schema of AgentDebugX.

Target schema (JSON object, no other top-level keys needed):
- trace_id: string (use the value given below)
- goal: string or null - the task the agent was trying to do, if inferable
- framework: string or null - the agent framework/source, if inferable
- metadata: object - anything useful that does not fit elsewhere
- events: array of event objects, chronological. Each event:
  - trace_id: MUST equal the top-level trace_id
  - event_id: short unique string (evt_1, evt_2, ...)
  - agent_name: which agent/component acted (default "agent")
  - event_type: one of {event_types}
  - step_index: integer 0,1,2,...
  - input: brief input summary (string or small object) or null
  - output: brief output summary or null
  - error: string if this step failed, else null
  - metadata: object for extra per-event fields

Rules:
1. Map every meaningful step of the source log to one event; preserve order.
2. Chat-style logs: user/system messages -> event_type "observation" (agent_name "user"/"system"), assistant messages -> "llm.response" (agent_name "assistant"), tool/function calls -> "tool.call"/"tool.result".
3. Put failure text in the event's "error" field, not only in output.
4. Trim long inputs/outputs to <= 600 characters each; never drop error text.
5. Output ONLY the JSON object - no markdown fences, no commentary.
6. If the payload does NOT describe an AI-agent run, a conversation, or a tool-use log (e.g. random tabular data, configs), output exactly: {{"error": "<one-line reason why this cannot be interpreted as an agent trajectory>"}}
"""


def convert_with_llm(
    payload: Any,
    trace_id: str,
    *,
    base_url: str = '',
    api_key: str = '',
    model: str = '',
) -> AgentTrajectory:
    """Best-effort LLM mapping of ``payload`` onto AgentTrajectory.

    If base_url+api_key are given they are used (a user's resolved creds);
    otherwise the server env config is used. Raises LLMConversionError with a
    user-facing reason on failure.
    """
    if base_url and api_key:
        client = OpenAICompatClient(
            base_url=base_url.rstrip('/').removesuffix('/chat/completions'),
            api_key=api_key,
            model=model or os.environ.get('AGENTDEBUG_LLM_MODEL', 'gpt-4o-mini'),
        )
    elif llm_available():
        client = OpenAICompatClient.from_env()
    else:
        raise LLMConversionError(
            'format not recognized and no server-side LLM is configured for auto-conversion'
        )
    client.default_max_tokens = 16384

    text = json.dumps(payload, ensure_ascii=False, default=str)
    truncated = False
    if len(text) > MAX_PAYLOAD_CHARS:
        text = text[:MAX_PAYLOAD_CHARS]
        truncated = True

    system = _CONVERT_INSTRUCTIONS.format(event_types=', '.join(EVENT_TYPES))
    user = (
        f'trace_id to use: {trace_id}\n'
        + ('NOTE: the payload below was truncated; convert what is visible.\n' if truncated else '')
        + 'Payload to convert:\n'
        + text
    )
    # Reasoning models (gemini-3.5-flash) occasionally spend the whole token
    # budget on hidden reasoning and return empty content; retry a couple of
    # times before giving up so a transient blank doesn't fail the upload.
    parsed = None
    last_reason = 'LLM converter returned no usable JSON'
    for _attempt in range(3):
        try:
            result = client.complete(
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                response_format={'type': 'json_object'},
                max_tokens=16384,
                timeout=150.0,
            )
        except Exception as exc:
            raise LLMConversionError(f'LLM converter request failed: {exc}') from exc
        candidate = _loads_lenient(result.text or '')
        if isinstance(candidate, dict):
            parsed = candidate
            break
        last_reason = (
            'LLM converter returned unparseable output'
            if (result.text or '').strip()
            else 'LLM converter returned empty output'
        )
    if not isinstance(parsed, dict):
        raise LLMConversionError(last_reason)
    if parsed.get('error') and not parsed.get('events'):
        raise LLMConversionError(f'not an agent trajectory: {parsed["error"]}')

    parsed['trace_id'] = trace_id
    events = parsed.get('events')
    if not isinstance(events, list) or not events:
        raise LLMConversionError('LLM converter produced no events')
    for event in events:
        if isinstance(event, dict):
            event['trace_id'] = trace_id
            if event.get('event_type') not in EVENT_TYPES:
                event['event_type'] = 'agent.step'
    metadata = parsed.get('metadata')
    parsed['metadata'] = dict(metadata) if isinstance(metadata, dict) else {}
    parsed['metadata']['converted_by'] = 'llm'
    parsed['metadata']['converter_model'] = client.model

    try:
        if hasattr(AgentTrajectory, 'model_validate'):
            return AgentTrajectory.model_validate(parsed)
        return AgentTrajectory.parse_obj(parsed)  # pydantic v1
    except Exception as exc:
        raise LLMConversionError(f'LLM output failed schema validation: {str(exc)[:300]}') from exc
