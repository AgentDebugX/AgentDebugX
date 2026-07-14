"""Server SDK for exposing an application-owned agent as a live runner."""

from __future__ import annotations

import importlib
import json
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from agentdebug.rerun.executors.http_live import HTTP_RUNNER_PROTOCOL_VERSION
from agentdebug.rerun.live_validation import parse_live_result
from agentdebug.rerun.request import (
    RerunCheckpoint,
    RerunDirective,
    RerunRequest,
)
from agentdebug.schema import AgentTrajectory, trajectory_from_json

LiveRunner = Callable[
    [RerunRequest, AgentTrajectory, threading.Event],
    dict[str, Any],
]


@dataclass(frozen=True)
class HttpRunnerCapabilities:
    """Capabilities advertised before AgentDebugX submits a live rerun."""

    runner: str
    framework: str
    checkpoint_policies: tuple[str, ...] = ('from_start',)
    environment_restore: bool = False
    cancellation: bool = True
    max_concurrency: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
            'runner': self.runner,
            'framework': self.framework,
            'live_execution': True,
            'checkpoint_policies': list(self.checkpoint_policies),
            'environment_restore': self.environment_restore,
            'cancellation': self.cancellation,
            'max_concurrency': self.max_concurrency,
            'metadata': dict(self.metadata),
        }


@dataclass
class _Job:
    run_id: str
    submission_id: str
    status: str = 'queued'
    created_at: str = field(default_factory=lambda: _utc_now())
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def status_payload(self) -> dict[str, Any]:
        return {
            'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
            'run_id': self.run_id,
            'submission_id': self.submission_id,
            'status': self.status,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'error': self.error,
        }


def create_http_runner_app(
    runner: LiveRunner,
    capabilities: HttpRunnerCapabilities,
    *,
    bearer_token: Optional[str] = None,
) -> Any:
    """Create a persistent FastAPI service around a real agent callback."""

    try:
        from fastapi import FastAPI, Header, HTTPException
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            'HTTP runner service requires `pip install agentdebugx[ui]`.'
        ) from exc
    if not capabilities.runner.strip() or not capabilities.framework.strip():
        raise ValueError('runner and framework capability names are required')
    if not capabilities.checkpoint_policies:
        raise ValueError('at least one checkpoint policy is required')
    if capabilities.max_concurrency < 1:
        raise ValueError('max_concurrency must be positive')

    jobs: dict[str, _Job] = {}
    submissions: dict[str, str] = {}
    lock = threading.RLock()
    pool = ThreadPoolExecutor(
        max_workers=capabilities.max_concurrency,
        thread_name_prefix='agentdebug-runner',
    )

    @asynccontextmanager
    async def lifespan(_app: Any):
        yield
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title=f'AgentDebugX Runner: {capabilities.runner}',
        version=HTTP_RUNNER_PROTOCOL_VERSION,
        lifespan=lifespan,
    )

    def authorize(authorization: Optional[str]) -> None:
        if bearer_token is None:
            return
        expected = f'Bearer {bearer_token}'
        if authorization is None or not secrets.compare_digest(
            authorization, expected
        ):
            raise HTTPException(status_code=401, detail='invalid runner token')

    def get_job(run_id: str) -> _Job:
        with lock:
            job = jobs.get(run_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f'unknown run_id: {run_id}')
        return job

    def execute(
        job: _Job,
        request: RerunRequest,
        source: AgentTrajectory,
    ) -> None:
        with lock:
            if job.cancel_event.is_set():
                job.status = 'cancelled'
                job.ended_at = _utc_now()
                return
            job.status = 'running'
            job.started_at = _utc_now()
        try:
            result = runner(request, source, job.cancel_event)
            parse_live_result(result)
            with lock:
                if job.cancel_event.is_set():
                    job.status = 'cancelled'
                    job.result = None
                else:
                    job.status = 'succeeded'
                    job.result = result
                job.ended_at = _utc_now()
        except Exception as exc:
            with lock:
                job.status = 'cancelled' if job.cancel_event.is_set() else 'failed'
                job.error = f'{exc.__class__.__name__}: {exc}'
                job.ended_at = _utc_now()

    @app.get('/healthz')
    def healthz() -> dict[str, Any]:
        return {'status': 'ok', 'runner': capabilities.runner}

    @app.get('/v1/capabilities')
    def get_capabilities(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return capabilities.to_dict()

    @app.post('/v1/reruns', status_code=202)
    def submit(
        payload: dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(
            default=None, alias='Idempotency-Key'
        ),
    ) -> dict[str, Any]:
        authorize(authorization)
        if payload.get('protocol_version') != HTTP_RUNNER_PROTOCOL_VERSION:
            raise HTTPException(status_code=400, detail='unsupported protocol_version')
        body_submission_id = str(payload.get('submission_id') or '').strip()
        header_submission_id = str(idempotency_key or '').strip()
        if (
            body_submission_id
            and header_submission_id
            and body_submission_id != header_submission_id
        ):
            raise HTTPException(
                status_code=400,
                detail='Idempotency-Key does not match submission_id',
            )
        submission_id = header_submission_id or body_submission_id
        if not submission_id or len(submission_id) > 200:
            raise HTTPException(
                status_code=400,
                detail='a bounded Idempotency-Key is required',
            )
        with lock:
            existing_run_id = submissions.get(submission_id)
            existing_job = jobs.get(existing_run_id) if existing_run_id else None
        if existing_job is not None:
            return existing_job.status_payload()
        try:
            request = _request_from_payload(payload.get('request'))
            source_payload = payload.get('source_trajectory')
            source = trajectory_from_json(json.dumps(source_payload))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'invalid rerun payload: {exc}') from exc
        if request.checkpoint.policy not in capabilities.checkpoint_policies:
            raise HTTPException(
                status_code=409,
                detail=f'unsupported checkpoint policy: {request.checkpoint.policy}',
            )
        run_id = f'http_run_{uuid4().hex}'
        job = _Job(run_id=run_id, submission_id=submission_id)
        with lock:
            jobs[run_id] = job
            submissions[submission_id] = run_id
        pool.submit(execute, job, request, source)
        return job.status_payload()

    @app.get('/v1/reruns/{run_id}')
    def status(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return get_job(run_id).status_payload()

    @app.post('/v1/reruns/{run_id}/cancel')
    def cancel(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        job = get_job(run_id)
        job.cancel_event.set()
        with lock:
            if job.status == 'queued':
                job.status = 'cancelled'
                job.ended_at = _utc_now()
        return job.status_payload()

    @app.get('/v1/reruns/{run_id}/trajectory')
    def trajectory(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        job = get_job(run_id)
        if job.status != 'succeeded' or job.result is None:
            raise HTTPException(
                status_code=409,
                detail=f'rerun result is not available: {job.status}',
            )
        return job.result

    return app


def load_live_runner(entrypoint: str) -> LiveRunner:
    """Load a ``module:function`` live runner callback."""

    module_name, separator, attribute_name = entrypoint.strip().partition(':')
    if not separator or not module_name or not attribute_name:
        raise ValueError('runner entrypoint must use module:function syntax')
    module = importlib.import_module(module_name)
    runner = getattr(module, attribute_name, None)
    if not callable(runner):
        raise ValueError(f'runner entrypoint is not callable: {entrypoint}')
    return runner


def serve_http_runner(
    entrypoint: str,
    capabilities: HttpRunnerCapabilities,
    *,
    host: str = '127.0.0.1',
    port: int = 8765,
    bearer_token: Optional[str] = None,
) -> None:
    """Serve an application-owned live runner until interrupted."""

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            'HTTP runner service requires `pip install agentdebugx[ui]`.'
        ) from exc
    app = create_http_runner_app(
        load_live_runner(entrypoint),
        capabilities,
        bearer_token=bearer_token,
    )
    uvicorn.run(app, host=host, port=port, log_level='info')


def _request_from_payload(payload: Any) -> RerunRequest:
    if not isinstance(payload, dict):
        raise ValueError('request must be an object')
    checkpoint = payload.get('checkpoint') or {}
    directive = payload.get('directive') or {}
    if not isinstance(checkpoint, dict) or not isinstance(directive, dict):
        raise ValueError('checkpoint and directive must be objects')
    return RerunRequest(
        trace_id=str(payload.get('trace_id') or ''),
        report_id=(
            str(payload['report_id']) if payload.get('report_id') is not None else None
        ),
        checkpoint=RerunCheckpoint(**checkpoint),
        directive=RerunDirective(**directive),
        metadata=(
            dict(payload.get('metadata'))
            if isinstance(payload.get('metadata'), dict)
            else {}
        ),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    'HttpRunnerCapabilities',
    'LiveRunner',
    'create_http_runner_app',
    'load_live_runner',
    'serve_http_runner',
]
