"""Live rerun executor for persistent HTTP agent environments."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from agentdebug.rerun.executors.base import LIVE_EXECUTION, RerunResult
from agentdebug.rerun.live_validation import parse_live_result
from agentdebug.rerun.request import RerunRequest
from agentdebug.schema import AgentTrajectory, model_to_dict

HTTP_RUNNER_PROTOCOL_VERSION = '1.0'
_TERMINAL_STATUSES = {'succeeded', 'failed', 'cancelled'}
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class HttpLiveExecutor:
    """Submit and monitor a real rollout in a persistent agent service."""

    id = 'http_live'
    execution_mode = LIVE_EXECUTION

    def __init__(
        self,
        base_url: str,
        source: AgentTrajectory,
        *,
        token: Optional[str] = None,
        timeout: float = 1800.0,
        poll_interval: float = 1.0,
        request_timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        verify_tls: bool = True,
        client: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized = normalize_http_runner_url(base_url)
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {'http', 'https'}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                'HTTP runner URL must be an absolute http(s) URL without credentials'
            )
        if (
            timeout <= 0
            or poll_interval < 0
            or request_timeout <= 0
            or max_retries < 0
            or retry_delay < 0
        ):
            raise ValueError('HTTP runner timeout values must be positive')
        self.base_url = normalized
        self.source = source
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.sleep = sleep
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self.client = client or httpx.Client(
            headers=headers,
            timeout=request_timeout,
            verify=verify_tls,
        )
        self._owns_client = client is None

    def capabilities(self) -> dict[str, Any]:
        response = self._request('GET', '/v1/capabilities')
        payload = _json_object(response, 'runner capabilities')
        if payload.get('protocol_version') != HTTP_RUNNER_PROTOCOL_VERSION:
            raise ValueError(
                'unsupported HTTP runner protocol_version: '
                f'{payload.get("protocol_version")!r}'
            )
        if payload.get('live_execution') is not True:
            raise ValueError('HTTP runner does not declare live_execution=true')
        if not str(payload.get('runner') or '').strip():
            raise ValueError('HTTP runner capabilities.runner is required')
        if not str(payload.get('framework') or '').strip():
            raise ValueError('HTTP runner capabilities.framework is required')
        checkpoints = payload.get('checkpoint_policies')
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError(
                'HTTP runner capabilities.checkpoint_policies must be non-empty'
            )
        return payload

    def run(self, request: RerunRequest) -> RerunResult:
        capabilities = self.capabilities()
        _validate_checkpoint_support(capabilities, request)
        submission_id = f'submission_{uuid4().hex}'
        submitted = self._request(
            'POST',
            '/v1/reruns',
            headers={'Idempotency-Key': submission_id},
            json={
                'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
                'submission_id': submission_id,
                'request': asdict(request),
                'source_trajectory': model_to_dict(self.source),
            },
        )
        submission = _json_object(submitted, 'rerun submission')
        run_id = str(submission.get('run_id') or '').strip()
        if not run_id:
            raise ValueError('HTTP runner submission did not return run_id')

        try:
            deadline = time.monotonic() + self.timeout
            status_payload = submission
            while str(status_payload.get('status') or '') not in _TERMINAL_STATUSES:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f'HTTP rerun {run_id} exceeded {self.timeout}s'
                    )
                self.sleep(self.poll_interval)
                response = self._request('GET', f'/v1/reruns/{run_id}')
                status_payload = _json_object(response, 'rerun status')

            status = str(status_payload.get('status'))
            if status != 'succeeded':
                detail = (
                    status_payload.get('error')
                    or status_payload.get('message')
                    or status
                )
                raise RuntimeError(f'HTTP rerun {run_id} {status}: {detail}')
            result_response = self._request(
                'GET', f'/v1/reruns/{run_id}/trajectory'
            )
            result_payload = _json_object(result_response, 'rerun result')
        except BaseException:
            self.cancel(run_id)
            raise
        trajectory, proof, metadata = parse_live_result(result_payload)
        trajectory.metadata.update(
            {
                'rerun_of': self.source.trace_id,
                'rerun_report_id': request.report_id,
                'rerun_policy': request.checkpoint.policy,
                'execution_mode': LIVE_EXECUTION,
                'observed_execution': True,
                'tools_executed': proof['tools_executed'],
                'live_runner': proof['runner'],
                'remote_run_id': run_id,
                'submission_id': submission_id,
            }
        )
        return RerunResult(
            request=request,
            trajectory=trajectory,
            metadata={
                **metadata,
                'executor': self.id,
                'execution_mode': LIVE_EXECUTION,
                'observed_execution': True,
                'tools_executed': proof['tools_executed'],
                'runner': proof['runner'],
                'framework': proof['framework'],
                'tool_execution_count': proof['tool_execution_count'],
                'remote_run_id': run_id,
                'submission_id': submission_id,
                'runner_url': self.base_url,
                'capabilities': capabilities,
            },
        )

    def cancel(self, run_id: str) -> None:
        try:
            self._request(
                'POST',
                f'/v1/reruns/{run_id}/cancel',
                retry=False,
            )
        except httpx.HTTPError:
            return

    def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        attempts = self.max_retries + 1 if retry else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(
                    method,
                    f'{self.base_url}{path}',
                    **kwargs,
                )
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt + 1 < attempts
                ):
                    self._wait_before_retry(attempt, response)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 >= attempts:
                    raise
                self._wait_before_retry(attempt)
        raise RuntimeError('HTTP runner request exhausted retries')

    def _wait_before_retry(
        self,
        attempt: int,
        response: Optional[httpx.Response] = None,
    ) -> None:
        retry_after = response.headers.get('Retry-After') if response else None
        try:
            delay = float(retry_after) if retry_after is not None else None
        except ValueError:
            delay = None
        self.sleep(
            max(0.0, delay)
            if delay is not None
            else min(self.retry_delay * (2 ** attempt), 10.0)
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> 'HttpLiveExecutor':
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _validate_checkpoint_support(
    capabilities: dict[str, Any],
    request: RerunRequest,
) -> None:
    checkpoints = capabilities.get('checkpoint_policies') or []
    if request.checkpoint.policy not in checkpoints:
        raise ValueError(
            f'HTTP runner does not support checkpoint policy '
            f'{request.checkpoint.policy!r}'
        )


def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f'{label} response is not valid JSON') from exc
    if not isinstance(payload, dict):
        raise ValueError(f'{label} response must be a JSON object')
    return payload


def normalize_http_runner_url(value: str) -> str:
    """Normalize a runner service root, accepting an accidental trailing /v1."""

    normalized = value.strip().rstrip('/')
    if normalized.endswith('/v1'):
        normalized = normalized[:-3].rstrip('/')
    return normalized


__all__ = [
    'HTTP_RUNNER_PROTOCOL_VERSION',
    'HttpLiveExecutor',
    'normalize_http_runner_url',
]
