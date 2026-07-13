"""Local trace-store commands."""

from agentdebug.cli.legacy import _cmd_list as list_traces
from agentdebug.cli.legacy import _cmd_show as show_trace

__all__ = ['list_traces', 'show_trace']
