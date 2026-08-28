"""Structured passive hook definitions for automatic capture."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

CAPTURE_STATUS_MESSAGE = 'agentdebug-capture'


def capture_hook_groups(
    events: Iterable[str], launcher: str
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        event: [
            {
                'hooks': [
                    {
                        'type': 'command',
                        'command': launcher,
                        'timeout': 1 if event == 'SessionEnd' else 10,
                        'statusMessage': CAPTURE_STATUS_MESSAGE,
                    }
                ]
            }
        ]
        for event in events
    }


def is_agentdebug_capture_group(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get('hooks')
    return bool(
        isinstance(hooks, list)
        and any(
            isinstance(hook, dict)
            and hook.get('statusMessage') == CAPTURE_STATUS_MESSAGE
            for hook in hooks
        )
    )
