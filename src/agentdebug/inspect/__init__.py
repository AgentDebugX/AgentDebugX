"""Inspection entry points."""

from agentdebug.core.plugins.registry import register_inspect_plugin
from agentdebug.inspect.traceback import CascadeFrame, build_cascade, format_traceback
from agentdebug.inspect.ui import build_app, build_overview, render_page, serve

__all__ = [
    'CascadeFrame',
    'build_app',
    'build_cascade',
    'build_overview',
    'format_traceback',
    'render_page',
    'serve',
]

register_inspect_plugin(
    'inspect.ui.local_dashboard',
    'Local Dashboard',
    version='1',
    capabilities=['overview', 'trace_detail', 'finding_inspection'],
    source_module='agentdebug.ui.server',
)
register_inspect_plugin(
    'inspect.traceback',
    'Traceback Renderer',
    version='1',
    capabilities=['text_traceback'],
    source_module='agentdebug.traceback',
)
