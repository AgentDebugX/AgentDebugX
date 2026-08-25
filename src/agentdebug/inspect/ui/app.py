"""Application entrypoints for the local inspection UI."""

from __future__ import annotations

import logging

from agentdebug.inspect.ui.routes import build_app
from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore, TraceStore

LOG = logging.getLogger('agentdebug.ui')

def serve(
    store: TraceStore,
    *,
    host: str = '127.0.0.1',
    port: int = 7777,
    run_registry: object = None,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            'AgentDebugX UI requires `uvicorn`. '
            'Install with `pip install agentdebugx[ui]`.'
        ) from exc
    app = build_app(store, run_registry=run_registry)
    LOG.info('Serving AgentDebugX console at http://%s:%s', host, port)
    uvicorn.run(app, host=host, port=port, log_level='warning')


def store_from_path(path: str) -> TraceStore:
    """Heuristic: ``.sqlite`` → SQLiteTraceStore; everything else → JSONL."""
    if path.endswith(('.sqlite', '.db')):
        return SQLiteTraceStore(path)
    return JsonlTraceStore(path)

