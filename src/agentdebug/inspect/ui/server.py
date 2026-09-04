"""Compatibility module for the AgentDebugX inspection UI.

The implementation is split across app, routes, views, services, and
branch_store. This module preserves the historical import path
``agentdebug.inspect.ui.server``.
"""

from __future__ import annotations

from agentdebug.inspect.ui.app import build_app, serve, store_from_path
from agentdebug.inspect.ui.branch_store import (
    CASE_DB_FILENAME,
    DEBUG_BRANCH_DB_FILENAME,
)
from agentdebug.inspect.ui.services import (
    build_overview,
)
from agentdebug.inspect.ui.views import render_page, render_space_page

__all__ = [
    'CASE_DB_FILENAME',
    'DEBUG_BRANCH_DB_FILENAME',
    'build_app',
    'build_overview',
    'render_page',
    'render_space_page',
    'serve',
    'store_from_path',
]
