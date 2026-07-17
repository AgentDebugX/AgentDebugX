"""Live rerun with a user-supplied MCP server as the execution environment (v1).

Instead of the default rerun (a one-shot LLM completion that *fabricates*
continuation events), this drives an LLM continuation loop from the checkpoint
where every tool call the model emits is dispatched to the user's own MCP
server and the REAL result is recorded. The rerun therefore executes against
the user's actual tools/environment.

Security posture (hosted, non-negotiable — see the design notes):
* SSRF guard: the MCP endpoint host is resolved and rejected if it maps to a
  loopback / private / link-local / cloud-metadata range; https is required
  unless the host is explicitly public. Embedded-credential and non-http(s)
  URLs are rejected.
* The MCP auth token is used transiently in-process only; it is NEVER logged,
  NEVER persisted into the stored trajectory/branch record, and only the MCP
  host (not the token) is written into trajectory metadata.
* A per-run wall-clock timeout and a max-tool-calls budget bound the work so a
  slow/hostile MCP server cannot hang the request indefinitely.

This module intentionally keeps its own small MCP (streamable-HTTP JSON-RPC)
client rather than pulling in a heavy dependency.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from agentdebug.core.llm import OpenAICompatClient, extract_json_block
from agentdebug.core.models import AgentEvent, AgentTrajectory, EventType

MAX_TOOL_CALLS_CAP = 40
DEFAULT_TIMEOUT_S = 300


class McpRerunError(ValueError):
    """User-facing error (bad config, SSRF rejection, MCP failure)."""


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #

def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable → block
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        # AWS/GCP/Azure metadata endpoint
        or str(addr) == '169.254.169.254'
    )


def validate_mcp_endpoint(
    endpoint: str,
    *,
    allow_insecure: bool = False,
    allow_private: bool = False,
) -> str:
    """Return the validated endpoint or raise McpRerunError.

    Rejects non-http(s), embedded credentials, and any host that resolves to a
    private/loopback/link-local/metadata address (SSRF protection).
    """
    endpoint = (endpoint or '').strip()
    if not endpoint:
        raise McpRerunError('mcp.endpoint is required for a live MCP rerun')
    parsed = urlparse(endpoint)
    if parsed.scheme not in ('http', 'https'):
        raise McpRerunError('mcp.endpoint must be an http(s) URL')
    if parsed.username or parsed.password or '@' in (parsed.netloc or ''):
        raise McpRerunError('mcp.endpoint must not embed credentials')
    if parsed.scheme == 'http' and not allow_insecure:
        raise McpRerunError('mcp.endpoint must use https')
    host = parsed.hostname
    if not host:
        raise McpRerunError('mcp.endpoint has no host')
    lowered = host.lower()
    if not allow_private and (
        lowered in ('localhost',)
        or lowered.endswith('.internal')
        or lowered.endswith('.local')
    ):
        raise McpRerunError('mcp.endpoint host is not allowed')
    # Resolve every address the host maps to and block if ANY is internal.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise McpRerunError(f'could not resolve mcp.endpoint host: {exc}') from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip) and not allow_private:
            raise McpRerunError('mcp.endpoint resolves to a non-public address (blocked)')
    return endpoint


# --------------------------------------------------------------------------- #
# Minimal MCP streamable-HTTP JSON-RPC client
# --------------------------------------------------------------------------- #

class McpClient:
    """Tiny MCP client: initialize → tools/list → tools/call over HTTP JSON-RPC."""

    def __init__(self, endpoint: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._id = 0
        self._session_id: Optional[str] = None
        base_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        if headers:
            base_headers.update(headers)
        self._client = httpx.Client(timeout=timeout, headers=base_headers, follow_redirects=False)

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None, *, notify: bool = False) -> Optional[Dict[str, Any]]:
        self._id += 1
        body: Dict[str, Any] = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self._id
        if params is not None:
            body['params'] = params
        extra = {'Mcp-Session-Id': self._session_id} if self._session_id else {}
        resp = self._client.post(self._endpoint, json=body, headers=extra)
        if 'Mcp-Session-Id' in resp.headers:
            self._session_id = resp.headers['Mcp-Session-Id']
        if notify:
            return None
        resp.raise_for_status()
        return self._parse_result(resp)

    def _parse_result(self, resp: httpx.Response) -> Dict[str, Any]:
        ctype = resp.headers.get('content-type', '')
        if 'text/event-stream' in ctype:
            # Take the last data: line carrying a JSON-RPC response.
            payload: Optional[Dict[str, Any]] = None
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith('data:'):
                    try:
                        candidate = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and ('result' in candidate or 'error' in candidate):
                        payload = candidate
            data = payload or {}
        else:
            data = resp.json()
        if isinstance(data, dict) and data.get('error'):
            raise McpRerunError(f"MCP error: {str(data['error'])[:200]}")
        result = data.get('result') if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}

    def initialize(self) -> None:
        self._rpc('initialize', {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'agentdebugx-rerun', 'version': '1.0'},
        })
        try:
            self._rpc('notifications/initialized', notify=True)
        except Exception:
            pass  # some servers don't require the notification

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self._rpc('tools/list', {})
        tools = result.get('tools') if isinstance(result, dict) else None
        return [t for t in (tools or []) if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """Return (text_result, is_error)."""
        result = self._rpc('tools/call', {'name': name, 'arguments': arguments or {}})
        is_error = bool(result.get('isError'))
        content = result.get('content')
        parts: List[str] = []
        for block in content or []:
            if isinstance(block, dict):
                if block.get('type') == 'text':
                    parts.append(str(block.get('text') or ''))
                else:
                    parts.append(json.dumps(block)[:500])
        text = '\n'.join(parts) if parts else json.dumps(result)[:800]
        return text, is_error

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Rerun orchestration
# --------------------------------------------------------------------------- #

_SYSTEM = """You are re-running an AI agent from a checkpoint in its trajectory, using a real toolset exposed over MCP.

You are given: the goal, the trajectory up to the checkpoint, a debugging directive describing what went wrong, and the list of tools you may call. Continue the agent's work from the checkpoint, calling tools to make real progress and fixing the failure the directive describes.

Respond with ONE JSON object per turn, nothing else:
- To call a tool:  {"action": "tool_call", "tool": "<tool_name>", "arguments": { ... }, "thought": "<one line why>"}
- To finish:       {"action": "final", "answer": "<final answer or outcome>", "thought": "<one line>"}
Only call tools that appear in the provided tool list. Keep arguments minimal and valid per each tool's schema."""


def _mcp_headers(auth: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(auth, dict):
        return {}
    kind = str(auth.get('type') or 'none').lower()
    token = str(auth.get('token') or '')
    if not token or kind == 'none':
        return {}
    if kind == 'bearer':
        return {'Authorization': f'Bearer {token}'}
    if kind == 'header':
        name = str(auth.get('header_name') or 'Authorization')
        return {name: token}
    return {}


def run_mcp_rerun(
    *,
    trajectory: AgentTrajectory,
    checkpoint_context: Dict[str, Any],
    directive: str,
    mcp_config: Dict[str, Any],
    llm: OpenAICompatClient,
) -> Dict[str, Any]:
    """Drive an LLM continuation loop that executes tools via the user's MCP server.

    Returns a dict with generated_events, tools_executed, mcp_server_host, and a
    transcript. Raises McpRerunError on config/SSRF/MCP failure.
    """
    allow_insecure = bool(mcp_config.get('allow_insecure'))
    allow_private = bool(mcp_config.get('allow_private'))
    endpoint = validate_mcp_endpoint(
        str(mcp_config.get('endpoint') or ''),
        allow_insecure=allow_insecure,
        allow_private=allow_private,
    )
    host = urlparse(endpoint).hostname or ''
    timeout_s = min(int(mcp_config.get('timeout_s') or DEFAULT_TIMEOUT_S), 600)
    max_tool_calls = min(int(mcp_config.get('max_tool_calls') or MAX_TOOL_CALLS_CAP), MAX_TOOL_CALLS_CAP)
    allowed_tools = mcp_config.get('allowed_tools') if isinstance(mcp_config.get('allowed_tools'), list) else None

    headers = _mcp_headers(mcp_config.get('auth'))
    client = McpClient(endpoint, headers=headers, timeout=min(timeout_s, 60))
    started = time.time()
    generated: List[Dict[str, Any]] = []
    transcript: List[Dict[str, Any]] = []
    tools_executed = 0
    try:
        client.initialize()
        tools = client.list_tools()
        if allowed_tools:
            tools = [t for t in tools if t.get('name') in allowed_tools]
        if not tools:
            raise McpRerunError('the MCP server exposed no usable tools')
        tool_menu = [
            {'name': t.get('name'), 'description': str(t.get('description') or '')[:300],
             'input_schema': t.get('inputSchema') or t.get('input_schema') or {}}
            for t in tools
        ]

        goal = trajectory.goal or ''
        prior = json.dumps(checkpoint_context, ensure_ascii=False, default=str)[:6000]
        convo = [
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content':
                f'GOAL: {goal}\n\nDEBUGGING DIRECTIVE (what to fix): {directive}\n\n'
                f'TRAJECTORY UP TO CHECKPOINT:\n{prior}\n\n'
                f'AVAILABLE TOOLS:\n{json.dumps(tool_menu, ensure_ascii=False)[:4000]}\n\n'
                'Continue from the checkpoint. Respond with one JSON action object.'},
        ]

        step = int(checkpoint_context.get('checkpoint_step_index') or 0) + 1
        while tools_executed < max_tool_calls and (time.time() - started) < timeout_s:
            result = llm.complete(messages=convo, response_format={'type': 'json_object'}, max_tokens=2048, timeout=90.0)
            decision = extract_json_block(result.text or '') or {}
            action = str(decision.get('action') or '').lower()
            thought = str(decision.get('thought') or '')[:300]

            if action == 'final' or not action:
                answer = str(decision.get('answer') or result.text or '')[:1200]
                generated.append(_event(trajectory.trace_id, step, 'assistant', EventType.LLM_RESPONSE,
                                         input=thought, output=answer))
                transcript.append({'type': 'final', 'answer': answer[:400]})
                break

            if action == 'tool_call':
                tool_name = str(decision.get('tool') or '')
                args = decision.get('arguments') if isinstance(decision.get('arguments'), dict) else {}
                generated.append(_event(trajectory.trace_id, step, 'assistant', EventType.TOOL_CALL,
                                         input={'tool': tool_name, 'arguments': args}, output=thought))
                step += 1
                if allowed_tools and tool_name not in allowed_tools:
                    err = f'tool {tool_name} is not in allowed_tools'
                    generated.append(_event(trajectory.trace_id, step, tool_name, EventType.TOOL_RESULT, error=err))
                    convo.append({'role': 'user', 'content': f'Tool {tool_name} is not allowed. Pick another.'})
                    step += 1
                    continue
                try:
                    text, is_error = client.call_tool(tool_name, args)
                except Exception as exc:  # noqa: BLE001
                    text, is_error = f'tool call failed: {str(exc)[:200]}', True
                tools_executed += 1
                generated.append(_event(
                    trajectory.trace_id, step, tool_name, EventType.TOOL_RESULT,
                    input={'tool': tool_name}, output=None if is_error else text[:1200],
                    error=text[:1200] if is_error else None))
                transcript.append({'type': 'tool', 'tool': tool_name, 'is_error': is_error, 'result': text[:300]})
                convo.append({'role': 'assistant', 'content': json.dumps(decision)[:600]})
                convo.append({'role': 'user', 'content': f'Tool result ({tool_name}, {"error" if is_error else "ok"}):\n{text[:1500]}\n\nContinue with the next JSON action.'})
                step += 1
                continue

            # Unknown action → nudge once.
            convo.append({'role': 'user', 'content': 'Respond with a valid action object ("tool_call" or "final").'})
    finally:
        client.close()

    return {
        'generated_events': generated,
        'tools_executed': tools_executed > 0,
        'tool_call_count': tools_executed,
        'mcp_server_host': host,
        'execution_mode': 'live_mcp',
        'transcript': transcript,
        'elapsed_ms': int((time.time() - started) * 1000),
    }


def _event(trace_id: str, step: int, agent: str, etype: EventType, *,
           input: Any = None, output: Any = None, error: Optional[str] = None) -> Dict[str, Any]:
    ev = AgentEvent(
        trace_id=trace_id, agent_name=agent, event_type=etype, step_index=step,
        input=input, output=output, error=error,
        metadata={'source': 'mcp_rerun'},
    )
    if hasattr(ev, 'model_dump'):
        return ev.model_dump(mode='json')
    return json.loads(ev.json())
