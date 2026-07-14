"""Lightweight recovery suggestions.

v0.1 ships ``ReflexionSuggestion`` — a *suggest-only* recovery generator that
produces a structured retry-prompt artifact based on Reflexion (Shinn et al.,
NeurIPS 2023, arXiv:2303.11366). Heavier strategies (Self-Refine loop, CRITIC,
Saga rollback, MCTS) are deferred per the roadmap and will land behind the same
:class:`Recoverer` protocol.

By design, **nothing here re-executes the agent** — recovery proposals are
artifacts to be surfaced (CLI/UI/PR comment) or fed back into the next run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple

from agentdebug.runtime import extract_json_block
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    FailureFinding,
    confidence_or_default,
    new_id,
)

if TYPE_CHECKING:
    from agentdebug.diagnose.context import DiagnoseContext


@dataclass
class FixProposal:
    proposal_id: str
    recoverer_id: str
    target_event_id: Optional[str]
    summary: str
    rationale: str
    confidence: float
    suggestion_text: str
    side_effects: List[str] = field(default_factory=list)
    requires_human_approval: bool = False


class Recoverer(Protocol):
    id: str

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        ...


def suggest_from_context(
    recoverer: Recoverer,
    context: 'DiagnoseContext',
) -> List[FixProposal]:
    """Run a recoverer against the primary attribution target when available."""

    return recoverer.suggest(context.trajectory, context.recovery_report)


def _looks_like_trajectory(value: object) -> bool:
    return hasattr(value, 'events') and hasattr(value, 'trace_id')


def _looks_like_report(value: object) -> bool:
    # Accept DiagnosticReport-compatible wrappers such as DeepDebugResult,
    # which proxy common report fields while carrying extra audit state.
    return hasattr(value, 'findings')


def _validate_suggest_args(
    recoverer_id: str,
    trajectory: object,
    report: object,
) -> None:
    if _looks_like_report(trajectory) and _looks_like_trajectory(report):
        raise TypeError(
            f'{recoverer_id}.suggest expects (trajectory, report); got '
            '(report, trajectory). Did you mean suggest(trajectory, report)?'
        )
    if not _looks_like_trajectory(trajectory):
        raise TypeError(
            f'{recoverer_id}.suggest expects an AgentTrajectory-like object '
            'as the first argument'
        )
    if not _looks_like_report(report):
        raise TypeError(
            f'{recoverer_id}.suggest expects a DiagnosticReport-like object '
            'as the second argument'
        )


class DeepDebugRecovery:
    """AgentDebugX's native recovery: DeepDebug's own diagnosis IS the fix.

    The DeepDebug agent's final turn already produces everything a retry
    needs — the localized root-cause step, quoted evidence, a plain-language
    explanation, and one concrete fix suggestion. This recoverer packages that
    output verbatim into a single retry directive, anchored on the root cause,
    with zero additional LLM calls.

    Contrast with the baseline strategies (:class:`ReflexionSuggestion`,
    :class:`CriticRecoverer`, :class:`AutoManualRules`), which are external
    self-correction methods that can run WITHOUT DeepDebug's diagnosis: they
    re-derive guidance from whatever findings they are given.
    ``DeepDebugRecovery`` is instead the end-to-end AgentDebugX method:
    multi-turn localization and the retry directive come from the same agent.
    """

    id = 'deepdebug'

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        if not report.findings:
            return []
        # Anchor on the root-cause finding (DeepDebug emits it first); other
        # findings are context, not separate proposals — one run, one directive.
        root = next(
            (f for f in report.findings
             if report.root_cause_step_index is not None
             and f.step_index == report.root_cause_step_index),
            report.findings[0],
        )
        goal = trajectory.goal or '(no goal recorded)'
        evidence_block = '\n'.join(f'  - {e}' for e in root.evidence) or '  (none)'
        fix = (
            root.suggestion
            or (report.suggestions[0] if report.suggestions else None)
            or 'Re-attempt the step with the diagnosed mistake corrected.'
        )
        why = report.summary or root.failure_mode.description
        directive = (
            f'Task: {goal}\n'
            f'Root cause (DeepDebug): step {root.step_index}, '
            f'agent {root.agent_name}\n'
            f'Why it failed: {why}\n'
            f'Evidence:\n{evidence_block}\n'
            f'Fix for the retry:\n  {fix}\n'
        )
        return [FixProposal(
            proposal_id=new_id('fix'),
            recoverer_id=self.id,
            target_event_id=root.event_id,
            summary=(
                f'DeepDebug retry directive for step {root.step_index} '
                f'({root.agent_name})'
            ),
            rationale=(
                'DeepDebug already localized the root cause and wrote the '
                'fix; the retry consumes its diagnosis directly.'
            ),
            confidence=min(0.95, max(0.2, confidence_or_default(root.confidence))),
            suggestion_text=directive,
            side_effects=['memory.write'],
            requires_human_approval=False,
        )]


class ReflexionSuggestion:
    """Emit a Reflexion-style retry reflection per finding.

    The output is purely textual — it can be appended to the agent's next
    system prompt, written to a project ``MANUAL.md``, or surfaced in the
    Console. There is no auto-apply.
    """

    id = 'reflexion'

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        if not report.findings:
            return []
        proposals: List[FixProposal] = []
        for finding in report.findings:
            proposals.append(self._build_proposal(trajectory, finding))
        return proposals

    def _build_proposal(
        self, trajectory: AgentTrajectory, finding: FailureFinding
    ) -> FixProposal:
        goal = trajectory.goal or '(no goal recorded)'
        framework = trajectory.framework or '(framework not declared)'
        evidence_block = '\n'.join(f'  - {e}' for e in finding.evidence) or '  (none)'
        suggestion_template = (
            finding.suggestion
            or (finding.failure_mode.suggestion_templates[0]
                if finding.failure_mode.suggestion_templates
                else 'Inspect the offending step and constrain the agent at that point.')
        )
        reflection = (
            f'Task: {goal}\n'
            f'Framework: {framework}\n'
            f'Observed failure mode: {finding.failure_mode.mode_id} '
            f'({finding.failure_mode.name})\n'
            f'Located at agent={finding.agent_name}, step={finding.step_index}, '
            f'event_id={finding.event_id}\n'
            f'Evidence:\n{evidence_block}\n'
            f'Next time, do the following:\n  {suggestion_template}\n'
        )
        return FixProposal(
            proposal_id=new_id('fix'),
            recoverer_id=self.id,
            target_event_id=finding.event_id,
            summary=(
                f'Reflexion retry hint for {finding.failure_mode.mode_id} '
                f'at step {finding.step_index}'
            ),
            rationale=(
                'Reflexion (Shinn et al., NeurIPS 2023) converts a failure '
                'into a verbal hint appended to next attempt.'
            ),
            confidence=min(0.9, max(0.1, confidence_or_default(finding.confidence))),
            suggestion_text=reflection,
            side_effects=['memory.write'],
            requires_human_approval=False,
        )


@dataclass(frozen=True)
class VerifierSpec:
    """A pattern describing a tool-grounded verifier that could have caught
    a particular family of failures.

    Used by :class:`CriticRecoverer` to recommend (not run) the addition of
    a verifier between the failing step and the next side-effect.
    """

    id: str
    description: str
    matches_families: tuple[str, ...]      # e.g. ('action',)
    matches_mode_prefixes: tuple[str, ...]  # e.g. ('action.format_error', 'action.parameter_error')
    suggested_code: str                      # short snippet showing how to add the guard
    rationale: str

    def matches(self, finding: 'FailureFinding') -> bool:
        if finding.failure_mode.family in self.matches_families:
            return True
        return any(
            finding.failure_mode.mode_id.startswith(p)
            for p in self.matches_mode_prefixes
        )


DEFAULT_VERIFIERS: List[VerifierSpec] = [
    VerifierSpec(
        id='json_schema_guard',
        description='Validate tool arguments against the tool JSON schema before execution.',
        matches_families=('action',),
        matches_mode_prefixes=('action.format_error', 'action.parameter_error'),
        suggested_code=(
            'from jsonschema import validate, ValidationError\n'
            'try:\n'
            '    validate(instance=tool_args, schema=tool.schema)\n'
            'except ValidationError as exc:\n'
            '    return handle_arg_error(exc, tool=tool, args=tool_args)\n'
        ),
        rationale=(
            'CRITIC (Gou et al., ICLR 2024): a tool-interactive verifier '
            'catches malformed/missing-argument failures before they hit '
            'the downstream API.'
        ),
    ),
    VerifierSpec(
        id='final_state_check',
        description='Independent final-state verifier confirms the task is satisfied before terminating.',
        matches_families=('verification', 'reflection'),
        matches_mode_prefixes=('verification.', 'reflection.progress_misjudge'),
        suggested_code=(
            'def verify_task_complete(goal: str, final_state: dict) -> bool:\n'
            '    # Check every explicit success criterion; do NOT trust the\n'
            '    # acting agent\'s self-report.\n'
            '    return all(criterion(final_state) for criterion in success_criteria(goal))\n'
            '\n'
            'if not verify_task_complete(goal, state):\n'
            '    trigger_recovery_planning(reason="task not verified complete")\n'
        ),
        rationale=(
            'MAST (Cemri et al., 2025) shows premature termination + missing '
            'task validation are dominant multi-agent failure modes. A '
            'verifier that does not trust the acting agent is the standard '
            'fix.'
        ),
    ),
    VerifierSpec(
        id='tool_result_typecheck',
        description='Type-check the tool result and require explicit handling of None / empty.',
        matches_families=('action', 'system'),
        matches_mode_prefixes=('action.wrong_tool', 'system.tool_execution_error'),
        suggested_code=(
            'result = tool.run(args)\n'
            'if result is None or result == {}:\n'
            '    return handle_empty_result(tool=tool, args=args)\n'
            'if not isinstance(result, tool.expected_return_type):\n'
            '    return handle_unexpected_type(result, tool=tool)\n'
        ),
        rationale=(
            'Tool outputs that the agent does not branch on (None, empty '
            'list, unexpected type) propagate as "agent hallucinated a '
            'fact" downstream. Force the agent to handle them at the call '
            'site.'
        ),
    ),
    VerifierSpec(
        id='handoff_context_contract',
        description='Require the receiving agent to restate critical constraints before proceeding.',
        matches_families=('multiagent',),
        matches_mode_prefixes=('multiagent.handoff_loss',),
        suggested_code=(
            'def handoff(payload: HandoffPayload, to_agent: Agent) -> None:\n'
            '    received = to_agent.read(payload)\n'
            '    if not received.restates(payload.constraints):\n'
            '        raise HandoffContractError(\n'
            '            "receiver did not restate constraints"\n'
            '        )\n'
        ),
        rationale=(
            'Who&When (Zhang et al., 2025): handoff context loss is the '
            'most common decisive multi-agent failure step. Typed handoff '
            'payloads + receiver restating prevent silent dropping.'
        ),
    ),
    VerifierSpec(
        id='loop_detector_guard',
        description='Detect repeated tool calls / no-progress windows and trigger replan.',
        matches_families=('planning',),
        matches_mode_prefixes=('planning.inefficient_plan',),
        suggested_code=(
            'from agentdebug.detectors import RepeatedToolCallDetector\n'
            'detector = RepeatedToolCallDetector(threshold=3)\n'
            'if detector.detect(current_trajectory):\n'
            '    return replan(reason="loop detected")\n'
        ),
        rationale=(
            'Loops are the canonical inefficient-plan failure. AgentDebugX '
            'ships an in-process detector you can call between steps.'
        ),
    ),
]


class CriticRecoverer:
    """Tool-grounded recovery suggestions modeled on CRITIC (arXiv:2305.11738).

    Unlike the paper's CRITIC loop (which re-runs the agent against a
    verifier until pass), this recoverer is suggest-only: for each finding
    it matches the failure mode against a registry of :class:`VerifierSpec`
    templates and emits a :class:`FixProposal` with the suggested verifier
    code + rationale. The user decides whether to add the verifier.

    Pair with :class:`ReflexionSuggestion` for full coverage: Reflexion
    tells the agent what went wrong; CriticRecoverer tells the developer
    what guard to add.
    """

    id = 'critic'

    def __init__(
        self,
        verifiers: Optional[List[VerifierSpec]] = None,
    ) -> None:
        self.verifiers = list(verifiers) if verifiers is not None else list(DEFAULT_VERIFIERS)

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        if not report.findings:
            return []
        proposals: List[FixProposal] = []
        seen: set[tuple[str, str]] = set()  # (event_id, verifier_id)
        for finding in report.findings:
            for verifier in self.verifiers:
                if not verifier.matches(finding):
                    continue
                key = (finding.event_id or '', verifier.id)
                if key in seen:
                    continue
                seen.add(key)
                proposals.append(self._build(finding, verifier))
        return proposals

    def _build(
        self, finding: FailureFinding, verifier: VerifierSpec,
    ) -> FixProposal:
        summary = (
            f'Add {verifier.id} before {finding.failure_mode.mode_id} '
            f'(step {finding.step_index}, agent {finding.agent_name})'
        )
        text = (
            f'Failure: {finding.failure_mode.mode_id} '
            f'({finding.failure_mode.name})\n'
            f'Located at agent={finding.agent_name}, step={finding.step_index}, '
            f'event_id={finding.event_id}\n\n'
            f'Recommended verifier: {verifier.id}\n'
            f'Rationale: {verifier.rationale}\n\n'
            f'Suggested code:\n'
            f'```python\n{verifier.suggested_code}\n```\n'
        )
        return FixProposal(
            proposal_id=new_id('fix'),
            recoverer_id=self.id,
            target_event_id=finding.event_id,
            summary=summary,
            rationale=verifier.rationale,
            confidence=max(0.3, min(0.85, confidence_or_default(finding.confidence))),
            suggestion_text=text,
            side_effects=[],
            requires_human_approval=False,
        )


_SELF_REFINE_CRITIC_PROMPT = """You are AgentDebugX-SelfRefine acting as the
CRITIC. You will be shown the goal of a failed agent run and a single
failure finding (mode + step + evidence).

Your job: in 2-4 short sentences, explain WHAT went wrong at that step and
WHY it caused the failure. Do not propose a fix yet — that's the next round.
Be concrete; reference the evidence.

Return ONLY a complete JSON object with this exact schema:
{"critic": "<2-4 complete sentences>"}
Do not output markdown or text outside the JSON object."""


_SELF_REFINE_REFINER_PROMPT = """You are AgentDebugX-SelfRefine acting as the
REFINER. You will be shown the goal of a failed agent run, a single failure
finding, AND the CRITIC's analysis of what went wrong.

Your job: produce a single concrete REFINED ACTION the agent should take
NEXT TIME at that step. 2-4 short sentences. Plain text. Be operational —
the agent will read this verbatim as guidance.

If the failure is in tool args, give the exact corrected arg shape. If the
failure is in planning, give the next plan step. If the failure is in
reflection, give the next verification check.

Return ONLY a complete JSON object with this exact schema:
{"refined_action": "<2-4 complete operational sentences>"}
Do not output markdown or text outside the JSON object."""


class SelfRefineLoop:
    """Self-Refine (Madaan et al., NeurIPS 2023, arXiv:2303.17651) recovery.

    Suggest-only — we do NOT re-execute the agent. For each finding, run a
    bounded generator-critic-refiner cycle: CRITIC explains what went
    wrong, REFINER proposes a concrete next-attempt action. The cycle can
    iterate ``max_iters`` times; each iteration costs 2 LLM calls.

    Output is a :class:`FixProposal` whose ``suggestion_text`` contains
    both the critic analysis and the refiner's concrete action. Pair with
    :class:`ReflexionSuggestion` for short verbal hints and
    :class:`CriticRecoverer` for verifier-template recommendations.
    """

    id = 'self_refine'

    def __init__(
        self,
        llm: object,
        *,
        max_iters: int = 1,
        max_tokens: int = 512,
    ) -> None:
        # llm is duck-typed to LLMClient (avoid circular import); it must have
        # a `.complete(messages=..., max_tokens=...)` returning .text.
        self.llm = llm
        self.max_iters = max(1, max_iters)
        self.max_tokens = max_tokens
        self._supports_response_format = True

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        if not report.findings:
            return []
        proposals: List[FixProposal] = []
        for finding in report.findings:
            proposal = self._refine_one(trajectory, finding)
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def _refine_one(
        self,
        trajectory: AgentTrajectory,
        finding: FailureFinding,
    ) -> Optional[FixProposal]:
        evidence_block = '\n'.join(f'  - {e}' for e in finding.evidence) or '  (none)'
        finding_block = (
            f'GOAL: {trajectory.goal!r}\n'
            f'FAILURE MODE: {finding.failure_mode.mode_id} '
            f'({finding.failure_mode.name})\n'
            f'AT: agent={finding.agent_name}, step={finding.step_index}\n'
            f'EVIDENCE:\n{evidence_block}'
            f'{_attribution_context_block(finding)}'
        )
        last_critic = ''
        last_refined = ''
        try:
            for _ in range(self.max_iters):
                critic_text = self._call(
                    system=_SELF_REFINE_CRITIC_PROMPT,
                    user=finding_block + (
                        f'\n\nPRIOR REFINED ACTION (improve on this if needed):\n'
                        f'{last_refined}' if last_refined else ''
                    ),
                    field='critic',
                )
                if not critic_text:
                    break
                last_critic = critic_text
                refined_text = self._call(
                    system=_SELF_REFINE_REFINER_PROMPT,
                    user=finding_block + f'\n\nCRITIC ANALYSIS:\n{critic_text}',
                    field='refined_action',
                )
                if not refined_text:
                    break
                last_refined = refined_text
        except Exception:
            # Defensive: never break the host pipeline on LLM hiccup.
            pass
        fallback_action = self._fallback_action(finding)
        if not self._looks_complete(last_refined):
            last_refined = fallback_action
        if not self._looks_complete(last_critic):
            last_critic = (
                f'The event at step {finding.step_index} introduced '
                f'{finding.failure_mode.name.lower()}; its output must be '
                'checked against the original task constraints before execution.'
            )
        suggestion_text = (
            f'Self-Refine output (after {self.max_iters} iter(s)) for '
            f'{finding.failure_mode.mode_id} at step {finding.step_index}:\n\n'
            f'CRITIC:\n{last_critic or "(no critic output)"}\n\n'
            f'REFINED ACTION:\n{last_refined or "(no refined action)"}'
        )
        return FixProposal(
            proposal_id=new_id('fix'),
            recoverer_id=self.id,
            target_event_id=finding.event_id,
            summary=(
                f'Self-Refine action for {finding.failure_mode.mode_id} '
                f'at step {finding.step_index}'
            ),
            rationale=(
                'Self-Refine (Madaan et al., NeurIPS 2023): one LM acts as '
                'critic, then refiner; output is a concrete next-attempt '
                'action the agent reads verbatim.'
            ),
            confidence=max(0.3, min(0.9, confidence_or_default(finding.confidence))),
            suggestion_text=suggestion_text,
            side_effects=[],
            requires_human_approval=False,
        )

    def _call(self, *, system: str, user: str, field: str) -> str:
        retry_budget = min(max(self.max_tokens * 4, self.max_tokens + 512), 8192)
        budgets = (self.max_tokens, retry_budget)
        for attempt, token_budget in enumerate(budgets):
            retry_note = (
                '\n\nYour previous response was truncated or invalid. '
                'Regenerate the complete JSON object from the beginning.'
                if attempt else ''
            )
            messages = [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user + retry_note},
            ]
            try:
                kwargs: Dict[str, Any] = {'max_tokens': token_budget}
                if self._supports_response_format:
                    kwargs['response_format'] = {'type': 'json_object'}
                result = self.llm.complete(  # type: ignore[attr-defined]
                    messages=messages,
                    **kwargs,
                )
            except Exception as exc:
                if self._response_format_unsupported(exc):
                    self._supports_response_format = False
                continue
            raw = getattr(result, 'raw', {}) or {}
            text = str(getattr(result, 'text', '') or '').strip()
            parsed = extract_json_block(text)
            value = str(parsed.get(field) or '').strip() if parsed else ''
            if not self._is_length_limited(raw) and self._looks_complete(value):
                return value
        return ''

    @staticmethod
    def _response_format_unsupported(exc: Exception) -> bool:
        message = str(exc).lower()
        return 'response_format' in message and any(
            marker in message
            for marker in ('unsupported', 'not support', 'unknown', 'invalid')
        )

    @staticmethod
    def _is_length_limited(raw: Dict[str, Any]) -> bool:
        choices = raw.get('choices') or []
        choice = choices[0] if choices else {}
        reason = str(
            choice.get('finish_reason')
            or choice.get('stop_reason')
            or raw.get('finish_reason')
            or raw.get('stop_reason')
            or ''
        ).strip().lower()
        return reason == 'length' or 'token' in reason

    @staticmethod
    def _looks_complete(text: str) -> bool:
        stripped = text.strip()
        return len(stripped) >= 40 and stripped.endswith(
            ('.', '!', '?', '"', "'", ')')
        )

    @staticmethod
    def _fallback_action(finding: FailureFinding) -> str:
        suggestion = (
            finding.suggestion
            or (finding.failure_mode.suggestion_templates[0]
                if finding.failure_mode.suggestion_templates else '')
        )
        action = suggestion.strip() or 'Correct the diagnosed mistake before retrying.'
        if not action.endswith(('.', '!', '?')):
            action += '.'
        return (
            f'Before retrying step {finding.step_index}: {action} '
            'Verify the corrected action against the original goal before any '
            'side-effecting tool call.'
        )


def _attribution_context_block(finding: FailureFinding) -> str:
    rationale = str(finding.metadata.get('attribution_rationale') or '').strip()
    sources = [str(item) for item in finding.metadata.get('attribution_sources') or []]
    if not rationale and not sources:
        return ''
    source_text = ', '.join(sources) if sources else '(unspecified)'
    return f'\nPRIMARY ATTRIBUTION: {rationale or "(no rationale)"}\nSOURCES: {source_text}'


class AutoManualRules:
    """AutoManual-style learned rule extraction (Chen et al., NeurIPS 2024,
    arXiv:2405.16247).

    Distills each failure finding into a one-line rule. ``suggest()`` returns
    one :class:`FixProposal` per finding whose ``suggestion_text`` holds the
    rule. ``apply()`` appends accepted rules to a per-project Markdown manual
    (``~/.agentdebug/manuals/<project>.md`` by default) which downstream
    runs can prepend to their agent's system prompt to avoid re-hitting the
    same failure.

    Unlike :class:`ReflexionSuggestion` (short verbal hint per attempt) and
    :class:`SelfRefineLoop` (heavy generator-critic-refiner per finding),
    this is the LONG-TERM memory: small, append-only, surviving across runs.
    Apply is opt-in because it writes to the filesystem.
    """

    id = 'auto_manual'

    DEFAULT_MANUAL_DIR = '~/.agentdebug/manuals'

    def __init__(
        self,
        *,
        llm: Optional[object] = None,
        manual_dir: Optional[str] = None,
        project: str = 'default',
        max_tokens: int = 256,
    ) -> None:
        # llm is optional — if absent, we synthesize a rule from the finding's
        # suggestion template rather than calling out.
        self.llm = llm
        self.manual_dir = manual_dir or self.DEFAULT_MANUAL_DIR
        self.project = project
        self.max_tokens = max_tokens

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        if not report.findings:
            return []
        proposals: List[FixProposal] = []
        for finding in report.findings:
            rule = self._rule_for(trajectory, finding)
            if not rule:
                continue
            proposals.append(FixProposal(
                proposal_id=new_id('fix'),
                recoverer_id=self.id,
                target_event_id=finding.event_id,
                summary=(
                    f'Add rule to {self.project} manual: '
                    f'"{rule[:80]}{"…" if len(rule) > 80 else ""}"'
                ),
                rationale=(
                    'AutoManual (Chen et al., NeurIPS 2024): distill the '
                    'failure into a one-line rule the next run reads via its '
                    'system prompt. Long-term memory complements per-attempt '
                    'Reflexion and per-finding Self-Refine.'
                ),
                confidence=max(0.4, min(0.9, confidence_or_default(finding.confidence))),
                suggestion_text=rule,
                side_effects=['manual.write'],
                requires_human_approval=False,
            ))
        return proposals

    def apply(self, proposal: FixProposal) -> str:
        """Append the proposal's rule to the project manual; return the path."""
        from pathlib import Path

        target = Path(self.manual_dir).expanduser() / f'{self.project}.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding='utf-8') if target.exists() else ''
        if not existing:
            header = f'# AgentDebugX learned manual — {self.project}\n\n'
            existing = header + '<!-- auto-generated; safe to edit. -->\n\n'
        # Idempotent: skip if the rule is already present (case-insensitive).
        rule = proposal.suggestion_text.strip()
        if rule.lower() in existing.lower():
            return str(target)
        target.write_text(
            existing + f'- {rule}\n',
            encoding='utf-8',
        )
        return str(target)

    def _rule_for(
        self,
        trajectory: AgentTrajectory,
        finding: FailureFinding,
    ) -> Optional[str]:
        # If no LLM, fall back to the finding's suggestion template.
        if self.llm is None:
            if finding.suggestion:
                return str(finding.suggestion)
            if finding.failure_mode.suggestion_templates:
                return str(finding.failure_mode.suggestion_templates[0])
            return None
        prompt_system = (
            'You distill failure findings into ONE-LINE actionable rules for '
            'an agent to follow next time. Output plain text — a single '
            'sentence under 200 characters. No JSON, no markdown, no '
            'preamble. Start with an imperative verb ("Validate ...", '
            '"Always ...", "Before ..."). Be specific to the failure.'
        )
        evidence = '; '.join(finding.evidence) or '(no evidence)'
        prompt_user = (
            f'Goal: {trajectory.goal!r}\n'
            f'Failure mode: {finding.failure_mode.mode_id} '
            f'({finding.failure_mode.name})\n'
            f'Agent: {finding.agent_name} at step {finding.step_index}\n'
            f'Evidence: {evidence}'
        )
        try:
            result = self.llm.complete(  # type: ignore[attr-defined]
                messages=[
                    {'role': 'system', 'content': prompt_system},
                    {'role': 'user', 'content': prompt_user},
                ],
                max_tokens=self.max_tokens,
            )
        except Exception:
            return str(finding.suggestion) if finding.suggestion else None
        text = getattr(result, 'text', '') or ''
        rule = str(text).strip().splitlines()[0] if text.strip() else ''
        if not rule:
            return str(finding.suggestion) if finding.suggestion else None
        return rule


@dataclass
class CompensationSpec:
    """Pairs a tool name with the function that undoes its side effect.

    The compensation receives the recorded TOOL_CALL input + TOOL_RESULT
    output and returns a short human-readable summary of what was
    rolled back. The function is the user's responsibility — AgentDebug
    only orchestrates the order and provenance.
    """

    tool_name: str
    description: str
    compensate: Callable[[Any, Any], str]


class Compensator:
    """Registry mapping tool_name → :class:`CompensationSpec`.

    Users register compensations once per tool the agent might call with a
    side effect (email send, DB write, API POST, file create). The Saga
    recoverer looks up compensations by the agent_name on a TOOL_CALL /
    TOOL_RESULT pair.
    """

    def __init__(self) -> None:
        self._specs: dict[str, CompensationSpec] = {}

    def register(
        self,
        tool_name: str,
        compensate: Callable[[Any, Any], str],
        *,
        description: str = '',
    ) -> CompensationSpec:
        spec = CompensationSpec(
            tool_name=tool_name,
            description=description or f'compensation for {tool_name}',
            compensate=compensate,
        )
        self._specs[tool_name] = spec
        return spec

    def get(self, tool_name: str) -> Optional[CompensationSpec]:
        return self._specs.get(tool_name)

    def registered_tools(self) -> list[str]:
        return sorted(self._specs.keys())


@dataclass
class _ToolPair:
    """A matched TOOL_CALL / TOOL_RESULT pair from a trajectory."""

    call_event_id: str
    result_event_id: str
    tool_name: str
    step_index: Optional[int]
    input: Any
    output: Any
    error: Optional[str]


def _pair_tool_calls(trajectory: AgentTrajectory) -> List[_ToolPair]:
    """Walk the trajectory and emit (call, result) pairs by agent_name +
    proximity. Calls without a following result are dropped (the tool never
    completed, so there's nothing to compensate)."""
    pairs: List[_ToolPair] = []
    pending: Dict[str, AgentEvent] = {}
    for evt in trajectory.events:
        et = getattr(evt.event_type, 'value', evt.event_type)
        if et == 'tool.call':
            pending[evt.agent_name] = evt
            continue
        if et == 'tool.result' and evt.agent_name in pending:
            call = pending.pop(evt.agent_name)
            pairs.append(_ToolPair(
                call_event_id=call.event_id,
                result_event_id=evt.event_id,
                tool_name=evt.agent_name,
                step_index=evt.step_index or call.step_index,
                input=call.input,
                output=evt.output,
                error=evt.error,
            ))
    return pairs


class SagaRollback:
    """SagaLLM-style compensating-action layer (arXiv:2503.11951).

    For every successful TOOL_CALL/TOOL_RESULT pair in the trajectory whose
    tool has a registered compensation, emit a :class:`FixProposal`. Tool
    results with an error are skipped — there's nothing to undo.

    Proposals are returned in REVERSE EXECUTION ORDER so applying them
    walks the side effects back to a clean state. Each ``apply()`` invokes
    the user-provided ``compensate(input, output)`` and returns the
    function's summary string.

    Unlike the suggest-only Reflexion / CRITIC / Self-Refine / AutoManual
    recoverers, ``apply()`` here HAS REAL SIDE EFFECTS — it runs the
    user's compensation code. Marked ``requires_human_approval=True`` by
    default so the UI surfaces every rollback for confirmation.
    """

    id = 'saga_rollback'

    def __init__(
        self,
        compensator: Compensator,
        *,
        require_approval: bool = True,
    ) -> None:
        self.compensator = compensator
        self.require_approval = require_approval

    def suggest(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
    ) -> List[FixProposal]:
        _validate_suggest_args(self.id, trajectory, report)
        # Recovery suggestions are independent of the report — saga always
        # looks at the trajectory's actual side-effect surface.
        del report
        pairs = _pair_tool_calls(trajectory)
        # Reverse order so consumers can apply in sequence.
        proposals: List[FixProposal] = []
        for pair in reversed(pairs):
            if pair.error:
                continue  # tool failed; no side effect to compensate
            spec = self.compensator.get(pair.tool_name)
            if spec is None:
                continue  # no registered compensation; can't auto-rollback
            proposals.append(FixProposal(
                proposal_id=new_id('fix'),
                recoverer_id=self.id,
                target_event_id=pair.result_event_id,
                summary=(
                    f'Compensate {pair.tool_name} call '
                    f'at step {pair.step_index}'
                ),
                rationale=(
                    f'SagaRollback (arXiv:2503.11951): undo the recorded '
                    f'side effect of {pair.tool_name}. '
                    f'Compensation: {spec.description}'
                ),
                confidence=0.85,
                suggestion_text=(
                    f'apply() will call the registered compensation for '
                    f'`{pair.tool_name}` with the recorded input/output, '
                    f'reversing the side effect.'
                ),
                side_effects=[f'tool.compensate:{pair.tool_name}'],
                requires_human_approval=self.require_approval,
            ))
        return proposals

    def apply(self, proposal: FixProposal, *, trajectory: AgentTrajectory) -> str:
        """Run the registered compensation. Returns the user's summary string.

        ``trajectory`` is required so apply can recover the recorded
        input/output from the target event without the caller having to
        carry them through.
        """
        pairs_by_result = {
            p.result_event_id: p for p in _pair_tool_calls(trajectory)
        }
        pair = pairs_by_result.get(proposal.target_event_id or '')
        if pair is None:
            raise ValueError(
                f'proposal target_event_id={proposal.target_event_id!r} not '
                f'found among tool-result events in the trajectory'
            )
        spec = self.compensator.get(pair.tool_name)
        if spec is None:
            raise ValueError(
                f'no registered compensation for tool {pair.tool_name!r}; '
                f'register it on the Compensator before applying'
            )
        return spec.compensate(pair.input, pair.output)


__all__ = [
    'AutoManualRules',
    'CompensationSpec',
    'Compensator',
    'CriticRecoverer',
    'DEFAULT_VERIFIERS',
    'FixProposal',
    'Recoverer',
    'ReflexionSuggestion',
    'SagaRollback',
    'SelfRefineLoop',
    'suggest_from_context',
    'VerifierSpec',
]
