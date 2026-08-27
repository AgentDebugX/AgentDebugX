"""Multi-granularity step compression, ported from TrajDebug's Stage A.

Every LLM detector in this package renders the trajectory by truncating each
event to a fixed character budget --- ``LLMJudgeAnalyzer`` at 300 characters,
``TrajDebugAnalyzer`` at 3000. That budget is spent uniformly, which is the
wrong allocation twice over:

* it is spent on the whole trajectory equally, including the parts far from
  wherever the failure is, and
* it is spent from the *front* of each field, so a 30,000-character tool result
  contributes its first 300 characters and loses the traceback at the end.

Stage A replaces the flat budget with a graded one. Each step is summarised at
three lengths (``th1`` detailed / ``th2`` moderate / ``th3`` terse), and the
renderer picks a tier per step according to how far that step is from the one
under judgement. Near steps arrive detailed, distant steps arrive as a gist,
and the total stays under a single cap.

Two short-circuits keep this affordable, both taken from the original:

1. a step already inside the smallest tier is passed through verbatim --- no
   call, because there is nothing to compress;
2. a machine-generated step (a diff, a traceback, terminal output) is clipped
   deterministically rather than summarised, because an LLM adds little to
   structured text and this is most of the volume on a coding trace.

Nothing here changes an existing default. The compressor is constructed
explicitly and handed to a detector; detectors built without one render exactly
as they did before.

Ported from TrajDebug (THU-KEG/TrajDebug, EMNLP 2026 Findings, MIT):
``detector/stage_a_diagnosis.py``, ``detector/utils/llm_compression.py``
(``_compress_step_unified_async``, ``clip_text_middle``) and
``detector/_stage_common.py`` (``select_tier_for_step``,
``render_history_for_focus``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

LOG = logging.getLogger('agentdebug.detect.compression')

TIERS = ('th1', 'th2', 'th3')

DEFAULT_TH1_CHARS = 1024
DEFAULT_TH2_CHARS = 512
DEFAULT_TH3_CHARS = 256

# TrajDebug's DEFAULT_COMPRESSED_HISTORY_OVERALL_CAP_CHARS. The graded tiers
# decide how the budget is *distributed*; this decides how large it is.
DEFAULT_OVERALL_CAP_CHARS = 12000

# Distance bands, in events, around the step under judgement.
DEFAULT_TH1_MAX_DISTANCE = 2
DEFAULT_TH2_MAX_DISTANCE = 5

_ELISION = '\n...[compressed]...\n'

# Event kinds whose payload is environment output rather than agent cognition.
# These are the ones worth clipping deterministically when they look
# machine-generated; an agent's own reasoning is always worth summarising.
_ENVIRONMENT_EVENT_TYPES = frozenset({
    EventType.TOOL_RESULT,
    EventType.OBSERVATION,
    EventType.ERROR,
    EventType.MEMORY_READ,
})


def clip_middle(text: str, limit: int) -> str:
    """Keep the head and the tail of ``text``, eliding the middle.

    The flat renderers use ``text[:limit]``, which throws away exactly the part
    of a tool result that says how it went. Head-plus-tail costs nothing and
    keeps both the call and its outcome. Ported from ``clip_text_middle``.
    """
    source = str(text or '')
    if len(source) <= limit:
        return source
    if limit <= 4:
        return source[:limit]
    # The marker has to come out of the same budget, so a small limit gets a
    # small marker rather than losing the tail it exists to protect.
    marker = _ELISION if limit >= 4 * len(_ELISION) else '…'
    budget = limit - len(marker)
    head = max(1, int(budget * 0.6))
    tail = budget - head
    return source[:head] + marker + (source[-tail:] if tail > 0 else '')


def looks_machine_generated(text: str) -> bool:
    """Heuristic for content an LLM would not usefully summarise.

    Diffs, tracebacks and terminal dumps compress fine by clipping: their
    structure is positional, so the head and tail carry the command and its
    result. Summarising them costs a call and loses the verbatim spans that
    evidence-grounded detectors need to quote.
    """
    if not text or not text.strip():
        return False
    body = text.strip()
    if 'diff --git' in body:
        return True
    if 'Traceback (most recent call last)' in body:
        return True
    if body.startswith('---') and '+++ ' in body[:500]:
        return True

    lines = body.split('\n')
    if len(lines) < 3:
        return False
    structured = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if (
            line.startswith(('    ', '\t', '+', '-', '|', '>'))
            or stripped.startswith(('$', '#', 'at ', 'File "'))
            or (len(stripped) > 2 and stripped[0].isdigit() and ':' in stripped[:8])
        ):
            structured += 1
    non_blank = sum(1 for line in lines if line.strip())
    return non_blank > 0 and structured / non_blank >= 0.6


def compress_role_for(event: AgentEvent) -> str:
    """Classify an event as ``compress`` (environment) or ``preserve`` (agent).

    TrajDebug carries this per message in its unified schema, baked in at
    dataset-build time. AgentDebugX has typed events instead, so the same
    distinction is derived from the event type rather than stored.
    """
    event_type = event.event_type
    try:
        typed = EventType(getattr(event_type, 'value', event_type))
    except ValueError:
        return 'preserve'
    return 'compress' if typed in _ENVIRONMENT_EVENT_TYPES else 'preserve'


def event_text(event: AgentEvent) -> str:
    """Render one event to the raw text the compressor and renderer operate on.

    Deliberately field-labelled rather than free prose: the detectors ask the
    model to quote spans back verbatim, so the text it reads has to be the text
    it can cite.
    """
    parts: List[str] = []
    for label, value in (
        ('input', event.input),
        ('output', event.output),
        ('error', event.error),
    ):
        if value in (None, '', {}, []):
            continue
        parts.append(f'{label}: {value}')
    if event.metadata:
        parts.append(f'metadata: {event.metadata}')
    return '\n'.join(parts)


def event_header(event: AgentEvent, position: int) -> str:
    """Header identifying a step, carrying the index the detector must report."""
    step = event.step_index if event.step_index is not None else position
    bits = [f'step={step}', f'type={getattr(event.event_type, "value", event.event_type)}']
    if event.agent_name:
        bits.append(f'agent={event.agent_name}')
    bits.append(f'event_id={event.event_id}')
    return '--- ' + ' '.join(bits) + ' ---'


_SYSTEM_PROMPT = """You are compressing ONE step of an agent trajectory at three
levels of detail, for downstream error diagnosis.

Produce a detailed version (th1), a moderate version (th2) and a concise version
(th3) of the SAME step. Every tier must stand alone as a description of what
happened at this step.

WHAT TO KEEP, in priority order:
1. Constraints and prohibitions -- limits, "must", "never", required formats,
   allowed and disallowed actions.
2. Plans and commitments -- ordered steps, sub-goals, what the agent said it
   would do next.
3. Actions taken and their outcomes -- the tool called, its arguments, whether
   it succeeded, and the error text if it did not.
4. Observations the agent must act on -- returned values, identifiers, paths,
   counts, states.
Drop prose, boilerplate, restated instructions and decorative formatting before
dropping any of the four above.

PRESERVE VERBATIM. Downstream diagnosis quotes substrings of your output
word-for-word as evidence, and an edited phrase fails that check. Copy exactly,
without translating, paraphrasing, reordering, rounding or abbreviating:
identifiers, file paths, function and variable names, error messages and
exception types, numbers, URLs, command lines, and any quoted string.

Never invent content that is not in the step. If the step is empty, say so.

LENGTHS (characters, per tier, hard caps):
  th1 <= {th1_chars}   detailed: every distinct fact, constraint and outcome.
  th2 <= {th2_chars}   moderate: merge related points, drop elaborations, keep
                       all constraints, decisions, outcomes and error text.
  th3 <= {th3_chars}   concise: the action, the result, the error, the binding
                       constraints. Nothing else.

OUTPUT RULES:
1. Output ONLY a JSON object. No prose before or after. No markdown fences.
2. Exactly three keys: "th1", "th2", "th3". All three are strings.
3. Emit the JSON object COMPLETE -- never stop mid-key or mid-string.

Schema:
{{"th1":"...","th2":"...","th3":"..."}}
"""


class StepCompressor:
    """Produce a three-tier summary of every event in a trajectory.

    One LLM call per event that needs one, issued concurrently. The two
    short-circuits mean a call is only spent on an event that is both large and
    genuinely prose --- on a coding trace that is a minority of the steps.

    ``stats`` records what happened so a run can report its own compression
    cost rather than leaving it to be inferred from a bill.
    """

    id = 'stage_a'

    def __init__(
        self,
        llm: LLMClient,
        *,
        th1_chars: int = DEFAULT_TH1_CHARS,
        th2_chars: int = DEFAULT_TH2_CHARS,
        th3_chars: int = DEFAULT_TH3_CHARS,
        max_tokens: int = 2048,
        max_workers: int = 8,
        request_json: bool = True,
    ) -> None:
        self.llm = llm
        self.th1_chars = th1_chars
        self.th2_chars = th2_chars
        self.th3_chars = th3_chars
        self.max_tokens = max_tokens
        self.max_workers = max(1, max_workers)
        self.request_json = request_json
        self.stats: Dict[str, int] = {
            'events': 0,
            'llm_calls': 0,
            'skipped_small': 0,
            'skipped_machine': 0,
            'parse_failures': 0,
        }

    @property
    def caps(self) -> Dict[str, int]:
        return {'th1': self.th1_chars, 'th2': self.th2_chars, 'th3': self.th3_chars}

    def compress(self, trajectory: AgentTrajectory) -> Dict[int, Dict[str, str]]:
        """Compress every event, keyed by position in ``trajectory.events``.

        Positional keys rather than ``step_index`` so the pool is well defined
        for trajectories whose importer did not set one; the rendered header
        still carries ``step_index``, which is what the detector reports.
        """
        events = list(trajectory.events)
        self.stats['events'] += len(events)
        if not events:
            return {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            tiers = list(pool.map(self._compress_event, events))
        return dict(enumerate(tiers))

    def _compress_event(self, event: AgentEvent) -> Dict[str, str]:
        text = event_text(event)

        # Short-circuit 1: already inside the smallest tier, nothing to do.
        if len(text) <= self.th3_chars:
            self.stats['skipped_small'] += 1
            return {tier: text for tier in TIERS}

        # Short-circuit 2: structured output clips better than it summarises.
        if compress_role_for(event) == 'compress' and looks_machine_generated(text):
            self.stats['skipped_machine'] += 1
            return {tier: clip_middle(text, cap) for tier, cap in self.caps.items()}

        parsed = self._call(text)
        if parsed is not None:
            return parsed
        return {tier: clip_middle(text, cap) for tier, cap in self.caps.items()}

    def _call(self, text: str) -> Optional[Dict[str, str]]:
        system = _SYSTEM_PROMPT.format(
            th1_chars=self.th1_chars,
            th2_chars=self.th2_chars,
            th3_chars=self.th3_chars,
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': text},
        ]
        kwargs: Dict[str, Any] = {'max_tokens': self.max_tokens}
        if self.request_json:
            kwargs['response_format'] = {'type': 'json_object'}
        self.stats['llm_calls'] += 1
        try:
            result = self.llm.complete(messages=messages, **kwargs)
        except Exception:
            LOG.warning('compression call failed; falling back to clipping', exc_info=True)
            self.stats['parse_failures'] += 1
            return None
        parsed = extract_json_block(result.text)
        if not isinstance(parsed, dict):
            self.stats['parse_failures'] += 1
            LOG.warning('compression returned no JSON; raw=%r', (result.text or '')[:200])
            return None
        out: Dict[str, str] = {}
        for tier, cap in self.caps.items():
            value = parsed.get(tier)
            if not isinstance(value, str) or not value.strip():
                self.stats['parse_failures'] += 1
                return None
            # The cap is instructed, not enforced by the model. Enforce it here
            # so a verbose tier cannot quietly blow the overall budget.
            out[tier] = clip_middle(value, cap)
        return out


def select_tier(
    distance: int,
    compress_role: str = 'preserve',
    *,
    th1_max_distance: int = DEFAULT_TH1_MAX_DISTANCE,
    th2_max_distance: int = DEFAULT_TH2_MAX_DISTANCE,
) -> str:
    """Pick a tier for a step ``distance`` events away from the focus.

    Environment steps are pinned to the terse tier regardless of distance: a
    tool dump next to the focus is still mostly noise, and the budget it would
    take is better spent on the agent's own turns. Ported from
    ``select_tier_for_step``.
    """
    if compress_role == 'compress':
        return 'th3'
    if distance <= th1_max_distance:
        return 'th1'
    if distance <= th2_max_distance:
        return 'th2'
    return 'th3'


def _pool_text(
    pool: Dict[int, Dict[str, str]],
    position: int,
    tier: str,
    event: AgentEvent,
    fallback_chars: int,
) -> str:
    entry = pool.get(position) or {}
    text = entry.get(tier)
    if isinstance(text, str) and text:
        return text
    # No compression for this step: clip rather than drop, so an incomplete
    # pool degrades to the old behaviour instead of losing the step.
    return clip_middle(event_text(event), fallback_chars)


def _assemble(
    events: Sequence[AgentEvent],
    tiers: Dict[int, str],
    pool: Dict[int, Dict[str, str]],
    caps: Dict[str, int],
    overall_cap_chars: int,
    focus_position: Optional[int],
) -> str:
    """Render the chosen tiers, then shrink to ``overall_cap_chars``.

    When the cap binds, the farthest steps give up their detail first and are
    dropped last, so the region under judgement keeps its resolution. This is
    the whole point of grading: the loss has to land somewhere, and it should
    not land on the step being judged.
    """
    positions = sorted(tiers)
    blocks: Dict[int, str] = {}
    for position in positions:
        event = events[position]
        tier = tiers[position]
        body = _pool_text(pool, position, tier, event, caps.get(tier, DEFAULT_TH3_CHARS))
        blocks[position] = f'{event_header(event, position)}\n{body}'

    def total() -> int:
        return sum(len(blocks[p]) for p in positions) + 2 * max(0, len(positions) - 1)

    def farthest(candidates: Iterable[int]) -> Optional[int]:
        anchor = focus_position if focus_position is not None else positions[-1]
        ordered = sorted(candidates, key=lambda p: (-abs(p - anchor), p))
        return ordered[0] if ordered else None

    # Shrink from the outside in. The farthest step is always the one that
    # gives something up: first its detail, then its place in the prompt. A
    # near step is only touched once everything beyond it is already gone,
    # which is what keeps the region under judgement at full resolution.
    dropped = 0
    while total() > overall_cap_chars and len(positions) > 1:
        candidates = [p for p in positions if p != focus_position]
        position = farthest(candidates)
        if position is None:
            break
        if tiers[position] != 'th3':
            event = events[position]
            tiers[position] = 'th3'
            body = _pool_text(
                pool, position, 'th3', event, caps.get('th3', DEFAULT_TH3_CHARS)
            )
            blocks[position] = f'{event_header(event, position)}\n{body}'
        else:
            positions.remove(position)
            blocks.pop(position, None)
            dropped += 1

    rendered = '\n\n'.join(blocks[p] for p in positions)
    if dropped:
        rendered += f'\n\n--- {dropped} more step(s) omitted to fit the context budget ---'
    return rendered or '(no history)'


def render_history_for_focus(
    events: Sequence[AgentEvent],
    focus_position: int,
    pool: Dict[int, Dict[str, str]],
    *,
    th1_max_distance: int = DEFAULT_TH1_MAX_DISTANCE,
    th2_max_distance: int = DEFAULT_TH2_MAX_DISTANCE,
    overall_cap_chars: int = DEFAULT_OVERALL_CAP_CHARS,
    caps: Optional[Dict[str, int]] = None,
    include_focus: bool = False,
    history_only_before: bool = True,
) -> str:
    """Render the trajectory around one step, graded by distance from it.

    The faithful port of TrajDebug's ``render_history_for_focus``: near steps
    detailed, far steps terse, and history-only by default so a step is never
    judged using text the agent had not yet seen.
    """
    caps = caps or {'th1': DEFAULT_TH1_CHARS, 'th2': DEFAULT_TH2_CHARS, 'th3': DEFAULT_TH3_CHARS}
    tiers: Dict[int, str] = {}
    for position, event in enumerate(events):
        if position == focus_position and not include_focus:
            continue
        if history_only_before and position > focus_position:
            continue
        tiers[position] = select_tier(
            abs(position - focus_position),
            compress_role_for(event),
            th1_max_distance=th1_max_distance,
            th2_max_distance=th2_max_distance,
        )
    if not tiers:
        return '(no history)'
    return _assemble(events, tiers, pool, caps, overall_cap_chars, focus_position)


class GradedContextBuilder:
    """Renders trajectory context for a chunk-based detector.

    The detectors in this package judge a *chunk* of events per call rather
    than a single focus step, so the grading is scoped to the chunk: everything
    under judgement arrives detailed, everything outside it arrives as a gist
    so the model still knows what surrounds the region. That is the same trade
    as the distance ladder, with the chunk standing in for the focus.

    Pass an instance as ``context_builder`` to ``LLMJudgeAnalyzer`` or
    ``TrajDebugAnalyzer``. Without one they render exactly as before.
    """

    def __init__(
        self,
        pool: Dict[int, Dict[str, str]],
        *,
        overall_cap_chars: int = DEFAULT_OVERALL_CAP_CHARS,
        caps: Optional[Dict[str, int]] = None,
        in_chunk_tier: str = 'th1',
        out_of_chunk_tier: str = 'th3',
    ) -> None:
        self.pool = {int(k): v for k, v in (pool or {}).items()}
        self.overall_cap_chars = overall_cap_chars
        self.caps = caps or {
            'th1': DEFAULT_TH1_CHARS,
            'th2': DEFAULT_TH2_CHARS,
            'th3': DEFAULT_TH3_CHARS,
        }
        self.in_chunk_tier = in_chunk_tier
        self.out_of_chunk_tier = out_of_chunk_tier

    def render_chunk(
        self, events: Sequence[AgentEvent], chunk: Sequence[AgentEvent]
    ) -> str:
        """Render all of ``events``, with those in ``chunk`` at full detail."""
        by_id = {id(event): position for position, event in enumerate(events)}
        in_chunk = {by_id[id(event)] for event in chunk if id(event) in by_id}
        if not in_chunk:
            return '(no history)'
        tiers = {
            position: (self.in_chunk_tier if position in in_chunk else self.out_of_chunk_tier)
            for position in range(len(events))
        }
        centre = (min(in_chunk) + max(in_chunk)) // 2
        return _assemble(
            events, tiers, self.pool, self.caps, self.overall_cap_chars, centre
        )

    def render_focus(self, events: Sequence[AgentEvent], focus_position: int, **kwargs: Any) -> str:
        return render_history_for_focus(
            events,
            focus_position,
            self.pool,
            overall_cap_chars=self.overall_cap_chars,
            caps=self.caps,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'pool': {str(k): v for k, v in self.pool.items()},
            'overall_cap_chars': self.overall_cap_chars,
            'caps': dict(self.caps),
            'in_chunk_tier': self.in_chunk_tier,
            'out_of_chunk_tier': self.out_of_chunk_tier,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'GradedContextBuilder':
        return cls(
            {int(k): v for k, v in (payload.get('pool') or {}).items()},
            overall_cap_chars=int(payload.get('overall_cap_chars', DEFAULT_OVERALL_CAP_CHARS)),
            caps=payload.get('caps'),
            in_chunk_tier=str(payload.get('in_chunk_tier', 'th1')),
            out_of_chunk_tier=str(payload.get('out_of_chunk_tier', 'th3')),
        )


__all__ = [
    'DEFAULT_OVERALL_CAP_CHARS',
    'DEFAULT_TH1_CHARS',
    'DEFAULT_TH2_CHARS',
    'DEFAULT_TH3_CHARS',
    'GradedContextBuilder',
    'StepCompressor',
    'TIERS',
    'clip_middle',
    'compress_role_for',
    'event_header',
    'event_text',
    'looks_machine_generated',
    'render_history_for_focus',
    'select_tier',
]
