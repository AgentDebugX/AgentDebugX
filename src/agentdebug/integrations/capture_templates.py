"""Structured passive hook definitions for automatic capture."""

from __future__ import annotations

from typing import Any, Dict, List

CAPTURE_STATUS_MESSAGE = 'agentdebug-capture'
CAPTURE_EVENTS = {
    'claude': [
        'SessionStart',
        'UserPromptSubmit',
        'Stop',
        'TaskCompleted',
        'SessionEnd',
    ],
    'codex': ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'],
}


def capture_hook_groups(platform: str, launcher: str) -> Dict[str, List[Dict[str, Any]]]:
    if platform not in CAPTURE_EVENTS:
        raise ValueError(f'unsupported capture platform: {platform}')
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
        for event in CAPTURE_EVENTS[platform]
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
