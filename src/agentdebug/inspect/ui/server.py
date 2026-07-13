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
    _append_case_record,
    _append_debug_branch_record,
    _case_db_path,
    _debug_branch_db_path,
    _delete_case_record,
    _read_case_records,
    _read_debug_branch_records,
    _write_debug_branch_records,
)
from agentdebug.inspect.ui.services import (
    _build_debug_continuation_context,
    _build_rerun_evaluation,
    _compact_event_payload,
    _decorate_debug_branch_record,
    _event_has_problem,
    _event_problem_text,
    _event_summary_for_prompt,
    _event_title,
    _extract_chat_content,
    _extract_generated_events,
    _extract_json_payload,
    _extract_partial_continuation_payload,
    _normalize_chat_endpoint,
    _normalize_generated_events,
    _request_debug_completion,
    _to_dict,
    build_overview,
)
from agentdebug.inspect.ui.views import (
    gui_embed_url,
    render_gui_page,
    render_page,
    render_space_page,
)

__all__ = [
    'CASE_DB_FILENAME',
    'DEBUG_BRANCH_DB_FILENAME',
    'build_app',
    'build_overview',
    'gui_embed_url',
    'render_gui_page',
    'render_page',
    'render_space_page',
    'serve',
    'store_from_path',
]
