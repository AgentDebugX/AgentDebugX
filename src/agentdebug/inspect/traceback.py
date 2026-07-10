"""Python-traceback-style rendering of cascading agent failures.

A diagnostic report contains a *set* of findings; what users actually want is
a *chain* that shows how a single root cause cascaded through later steps,
ending at the manifested failure — exactly the way a Python traceback walks
from the outermost frame to the raised exception.

Two inputs feed the chain:

* DeepDebug findings populate ``finding.metadata['cascading_from_event_id']``
  with the predecessor's event ID, so the cascade is explicit and verified.
* Heuristic / single-shot LLM judges don't compute a cascade, so we fall
  back to **step-index ordering** with the earliest finding as the root.

Public API::

    from agentdebug.traceback import format_traceback
    print(format_traceback(report, trajectory))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agentdebug.core.models import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    FailureFinding,
    confidence_or_default,
)


@dataclass
class CascadeFrame:
    """One frame in the cascade — analogous to one Python traceback line."""

    finding: FailureFinding
    event: Optional[AgentEvent]
    cascades_from_event_id: Optional[str] = None
    depth: int = 0  # 0 = root cause; deepest = manifested failure
    children_event_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cascade construction
# ---------------------------------------------------------------------------

def build_cascade(
    report: DiagnosticReport,
    trajectory: Optional[AgentTrajectory] = None,
) -> List[CascadeFrame]:
    """Build an ordered chain of frames (root → manifested) from a report.

    Uses ``finding.metadata['cascading_from_event_id']`` when present;
    otherwise falls back to step-index ordering.
    """
    if not report.findings:
        return []

    events_by_id: Dict[str, AgentEvent] = {}
    if trajectory is not None:
        for evt in trajectory.events:
            events_by_id[evt.event_id] = evt

    # Group findings by event_id so duplicates collapse cleanly.
    by_event: Dict[str, List[FailureFinding]] = {}
    orphans: List[FailureFinding] = []
    for f in report.findings:
        if f.event_id:
            by_event.setdefault(f.event_id, []).append(f)
        else:
            orphans.append(f)

    # Extract predecessor links.
    predecessor: Dict[str, Optional[str]] = {}
    for event_id, findings in by_event.items():
        # Best predecessor wins (any non-null among the findings on this event).
        cand: Optional[str] = None
        for f in findings:
            meta_value = f.metadata.get('cascading_from_event_id')
            if isinstance(meta_value, str) and meta_value:
                cand = meta_value
                break
        predecessor[event_id] = cand

    # Determine root: prefer report.root_cause_event_id, else the finding with
    # the smallest step_index (None pushed to the end), then highest confidence.
    root_event_id: Optional[str] = report.root_cause_event_id
    if root_event_id not in by_event:
        ordered = sorted(
            by_event.items(),
            key=lambda kv: (
                _min_step(kv[1]) is None,
                _min_step(kv[1]) if _min_step(kv[1]) is not None else 10**9,
                -max(confidence_or_default(f.confidence) for f in kv[1]),
            ),
        )
        root_event_id = ordered[0][0] if ordered else None

    chain_event_ids: List[str] = []
    if root_event_id is not None:
        # Walk descendants from root using the predecessor map (reverse it).
        descendants: Dict[str, List[str]] = {}
        for child, parent in predecessor.items():
            if parent and parent in by_event:
                descendants.setdefault(parent, []).append(child)

        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            chain_event_ids.append(node)
            # Sort children by step_index so we walk forward in time.
            children = sorted(
                descendants.get(node, []),
                key=lambda eid: _min_step(by_event[eid]) or 10**9,
            )
            for child in children:
                dfs(child)

        dfs(root_event_id)

        # Any disconnected findings: append by step order at the end.
        leftover = [
            eid for eid in by_event
            if eid not in visited
        ]
        leftover.sort(
            key=lambda eid: _min_step(by_event[eid]) or 10**9
        )
        chain_event_ids.extend(leftover)
    else:
        # No structural cascade info — fall back to step order.
        chain_event_ids = sorted(
            by_event.keys(),
            key=lambda eid: _min_step(by_event[eid]) or 10**9,
        )

    frames: List[CascadeFrame] = []
    for depth, event_id in enumerate(chain_event_ids):
        # If multiple findings on this event, emit one frame per finding but
        # group them — earliest-confidence-tiebreak first.
        ranked = sorted(
            by_event[event_id],
            key=lambda f: -confidence_or_default(f.confidence),
        )
        for f in ranked:
            frames.append(CascadeFrame(
                finding=f,
                event=events_by_id.get(event_id),
                cascades_from_event_id=predecessor.get(event_id),
                depth=depth,
            ))
    # Orphan findings (no event_id) appended at the end.
    for f in orphans:
        frames.append(CascadeFrame(
            finding=f, event=None, cascades_from_event_id=None,
            depth=len(chain_event_ids),
        ))
    return frames


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_traceback(
    report: DiagnosticReport,
    trajectory: Optional[AgentTrajectory] = None,
    *,
    use_color: bool = False,
    indent: str = '    ',
) -> str:
    """Render a cascading agent-failure traceback.

    Output mirrors Python's traceback shape: a header, frames ordered
    *root → manifested*, then a final summary line that names the failure.
    """
    frames = build_cascade(report, trajectory)
    if not frames:
        return _wrap_color(
            'AgentTraceback: no findings recorded.',
            'muted',
            use_color,
        )

    lines: List[str] = []
    header = 'AgentTraceback (root cause first, manifested failure last):'
    lines.append(_wrap_color(header, 'header', use_color))
    if trajectory is not None:
        meta = []
        if trajectory.trace_id:
            meta.append(f'trace_id={trajectory.trace_id}')
        if trajectory.framework:
            meta.append(f'framework={trajectory.framework}')
        if trajectory.goal:
            meta.append(f'goal={trajectory.goal!r}')
        if meta:
            lines.append(indent + _wrap_color('  '.join(meta), 'meta', use_color))
        lines.append('')

    for idx, frame in enumerate(frames):
        lines.extend(_format_frame(frame, indent=indent, use_color=use_color))
        if idx < len(frames) - 1:
            lines.append(indent + _wrap_color('↓ cascaded to', 'arrow', use_color))

    # Tail summary — analogue to "TypeError: ..." in Python tracebacks.
    final = frames[-1].finding
    summary = report.summary or final.failure_mode.name
    tail = (
        f'AgentFailure[{final.failure_mode.mode_id}]: '
        f'{summary}'
    )
    lines.append('')
    lines.append(_wrap_color(tail, 'failure', use_color))
    return '\n'.join(lines)


def _format_frame(
    frame: CascadeFrame, *, indent: str, use_color: bool
) -> List[str]:
    f = frame.finding
    event = frame.event
    role = 'root cause' if frame.depth == 0 else f'cascade depth {frame.depth}'
    header_parts = [
        f'Step {f.step_index if f.step_index is not None else "?"}',
        f'agent={f.agent_name or "?"}',
        f'mode={f.failure_mode.mode_id}',
    ]
    if f.confidence is not None:
        header_parts.append(f'confidence={f.confidence:.2f}')
    header = f'  File "{role}", in trajectory'
    sub = f'    {"  ".join(header_parts)}'

    lines: List[str] = [
        indent + _wrap_color(header, 'frame', use_color),
        indent + _wrap_color(sub, 'frame-meta', use_color),
    ]
    if event is not None:
        if event.module:
            lines.append(indent + f'      module={event.module}')
        if event.event_id:
            lines.append(indent + f'      event_id={event.event_id}')
        if event.input is not None and str(event.input).strip():
            lines.append(indent + f'      input>  {_truncate(event.input)}')
        if event.output is not None and str(event.output).strip():
            lines.append(indent + f'      output> {_truncate(event.output)}')
        if event.error:
            lines.append(
                indent
                + _wrap_color(f'      error>  {_truncate(event.error)}', 'error', use_color)
            )
    if f.evidence:
        lines.append(indent + '      evidence:')
        for ev in f.evidence:
            lines.append(indent + f'        - {_truncate(ev, 220)}')
    if f.suggestion:
        lines.append(
            indent
            + _wrap_color(f'      suggested: {_truncate(f.suggestion, 220)}', 'suggestion', use_color)
        )
    return lines


def _truncate(value: object, max_chars: int = 160) -> str:
    text = '' if value is None else str(value)
    text = text.replace('\n', ' ')
    if len(text) > max_chars:
        return text[:max_chars] + '…'
    return text


def _min_step(findings: List[FailureFinding]) -> Optional[int]:
    steps = [f.step_index for f in findings if f.step_index is not None]
    return min(steps) if steps else None


# ---------------------------------------------------------------------------
# Tiny ANSI colorization (no dep)
# ---------------------------------------------------------------------------

_PALETTE = {
    'header':     '\033[1;37m',   # bold white
    'meta':       '\033[2m',      # dim
    'frame':      '\033[1;36m',   # cyan, bold
    'frame-meta': '\033[36m',     # cyan
    'arrow':      '\033[2;33m',   # dim yellow
    'error':      '\033[31m',     # red
    'suggestion': '\033[32m',     # green
    'failure':    '\033[1;31m',   # bold red
    'muted':      '\033[2m',
}
_RESET = '\033[0m'


def _wrap_color(text: str, style: str, use_color: bool) -> str:
    if not use_color:
        return text
    code = _PALETTE.get(style)
    if not code:
        return text
    return f'{code}{text}{_RESET}'


__all__ = ['CascadeFrame', 'build_cascade', 'format_traceback']
