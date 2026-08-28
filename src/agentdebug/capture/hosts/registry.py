"""Registry for supported automatic-capture hosts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from agentdebug.capture.context import (
    CURRENT_CAPTURE_CONTEXT_ENV,
    CurrentCaptureContext,
    read_current_capture_context,
    validate_current_capture_context,
)
from agentdebug.capture.hosts.base import CaptureHost
from agentdebug.capture.hosts.claude_code import ClaudeCodeCaptureHost
from agentdebug.capture.hosts.codex import CodexCaptureHost


_HOSTS = (ClaudeCodeCaptureHost(), CodexCaptureHost())
_BY_CLI_NAME = {host.cli_name: host for host in _HOSTS}
_BY_HOST_NAME = {host.host_name: host for host in _HOSTS}


def get_capture_host(platform: str) -> CaptureHost:
    try:
        return _BY_CLI_NAME[platform]
    except KeyError as exc:
        raise ValueError(f'unsupported capture platform: {platform}') from exc


def get_capture_host_by_name(host_name: str) -> CaptureHost:
    try:
        return _BY_HOST_NAME[host_name]
    except KeyError as exc:
        raise ValueError(f'unsupported capture host: {host_name}') from exc


def resolve_current_capture_context(
    *, environ: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None
) -> CurrentCaptureContext:
    values = os.environ if environ is None else environ
    working_directory = (cwd or Path.cwd()).expanduser().resolve()
    explicit_context_path = values.get(CURRENT_CAPTURE_CONTEXT_ENV)
    if explicit_context_path:
        explicit_context = read_current_capture_context(explicit_context_path)
        host = get_capture_host_by_name(explicit_context.host)
        context = host.resolve_current_context(values, working_directory)
        if context is None:
            raise ValueError(
                f'current capture context is unavailable for {host.host_name}'
            )
        return validate_current_capture_context(context, cwd=working_directory)
    matches = [
        context
        for host in _HOSTS
        if (context := host.resolve_current_context(values, working_directory))
        is not None
    ]
    if not matches:
        raise ValueError(
            'no current captured session context; provide a trajectory or invoke '
            'AgentDebugX from a supported host plugin'
        )
    if len(matches) > 1:
        raise ValueError('multiple current captured session contexts are active')
    return validate_current_capture_context(matches[0], cwd=working_directory)


__all__ = [
    'get_capture_host',
    'get_capture_host_by_name',
    'resolve_current_capture_context',
]
