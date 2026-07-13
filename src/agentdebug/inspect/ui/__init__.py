"""Optional dashboard for AgentDebugX.

Run with::

    pip install agentdebugx[ui]
    agentdebug serve --store-sqlite .agentdebug/errors.sqlite

The server is a small FastAPI app + a no-build HTML/JS frontend served from
``GET /``. Everything is local-only (loopback) by default.
"""

from agentdebug.inspect.ui.server import build_app, build_overview, render_page, serve

__all__ = ['build_app', 'build_overview', 'render_page', 'serve']
