# Inspect Workflow

Inspect provides human-facing views over traces, Diagnose reports, and related
artifacts.

## When to use

Use Inspect when a developer needs to explore AgentDebugX artifacts manually
instead of reading raw JSON.

## Flow

1. Load stored traces or report artifacts.
2. Build a traceback-style or UI-oriented representation.
3. Serve data through the local inspection API when requested.
4. Keep the inspected artifact unchanged unless an explicit write operation is
   requested.

## UI boundaries

The FastAPI UI is a surface layer:

- `ui/app.py` assembles the application.
- `ui/routes.py` defines HTTP endpoints and server-side Rerun policy.
- `ui/views.py` owns HTML, CSS, and browser behavior.
- `ui/services.py` adapts Diagnose and Rerun workflows for the UI.
- `ui/branch_store.py` owns local case and debug-branch persistence.
- `ui/server.py` remains a compatibility import path.

## Report selection

The UI displays the newest stored diagnostic report for a trace by default.
When a trace has multiple reports, the Trace Editor exposes a report selector
and keeps the selected `report_id` attached to saved cases, continuation
requests, and rerun sessions. A heuristic report is generated only when the
store has no report for that trace; the UI labels this as a heuristic fallback.

Unknown trace, event, report, case, and debug-session identifiers return an
explicit `404` response instead of silently opening a different view.

Live UI reruns prefer `AGENTDEBUG_RUNNER_URL` and may use
`AGENTDEBUG_RERUN_COMMAND` as a process fallback. They default to `from_start`.
True `from_event` execution requires both runner capability and
`AGENTDEBUG_UI_RERUN_POLICY=from_event`. Runner credentials and commands stay on
the server and are never supplied by the browser.

The runtime status popover reports whether a live runner is configured without
returning runner URLs, tokens, or commands to the browser. When no runner is
available, users can still copy or download the prepared rerun request, but the
live execution control is disabled.

## Local data and security

The built-in server binds to `127.0.0.1` by default. Keep that default unless
the UI is placed behind authentication and transport security; the local UI
does not implement user accounts or remote-access authorization.

Case records are stored in `typical_error_cases.jsonl`. Debug and rerun session
records are stored in `.agentdebug/debug_branches.jsonl`. Create, update, and
delete actions are persisted by the server rather than only in browser storage.
The built-in single-process server serializes these JSONL writes to prevent
concurrent UI actions from dropping records.

UI responses disable caching because rendered pages and API payloads can
contain complete trajectories. The server also emits restrictive content,
framing, MIME-sniffing, and referrer headers.

## Dependencies

Traceback-style inspection is local. The UI server requires the `ui` extra,
which installs FastAPI and Uvicorn.

## Extension Rules

- Keep display logic separate from schema models.
- Treat inspection as read-only by default.
- Put API and web serving code under `inspect/ui/`.
- Do not make Diagnose depend on Inspect.
