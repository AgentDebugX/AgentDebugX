"""Root cause vs. visible manifestation in a multi-agent cascade.

This is the central story behind AgentDebugX: in real agent failures the step
that *crashes* is rarely the step that is *responsible*. Here a planner silently
drops a hard constraint ("refundable") at step 1; the error only surfaces four
steps later as a schema-validation crash in the browser tool. A naive reader
blames the crash; good attribution blames the planner.

Run offline (no LLM, deterministic)::

    PYTHONPATH=src python examples/multi_agent_cascade.py

Add an LLM judge + attribution (semantic root-cause) by exporting::

    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'
    export AGENTDEBUG_LLM_API_KEY='sk-...'
    export AGENTDEBUG_LLM_MODEL='gemini-3-flash'   # optional
"""

from __future__ import annotations

import os

from agentdebug import AgentDebug, EventType
from agentdebug.attribution import HeuristicAttributor, StepByStepAttributor
from agentdebug.judges import LLMJudgeAnalyzer
from agentdebug.llm import OpenAICompatClient
from agentdebug.recovery import ReflexionSuggestion


def build_cascade_trace() -> tuple[AgentDebug, object]:
    """A 5-step cascade whose gold root cause is step 1, not the step-5 crash."""
    dbg = AgentDebug()
    t = dbg.start_trace(
        goal='Book a *refundable* flight NYC->SFO under $400',
        framework='multi-agent',
    )
    # Step 1 (planner): the real root cause -- the constraint is dropped here.
    dbg.record_event(
        t, EventType.PLAN, agent_name='planner', module='planning', step_index=1,
        output='Plan: search flights NYC->SFO, pick cheapest, checkout. '
               "(omits the 'refundable' constraint)",
    )
    # Step 2 (planner): propagates the lossy handoff.
    dbg.record_event(
        t, EventType.LLM_RESPONSE, agent_name='planner', module='planning', step_index=2,
        output="Handing off to booking agent: 'find cheapest NYC->SFO'",
    )
    # Step 3 (search): does exactly what it was told -- locally correct.
    dbg.record_event(
        t, EventType.TOOL_CALL, agent_name='search', module='action', step_index=3,
        input={'q': 'cheapest NYC SFO'}, output='Found UA123 $312 non-refundable',
    )
    # Step 4 (booking): also locally correct given the bad plan.
    dbg.record_event(
        t, EventType.OBSERVATION, agent_name='booking', module='action', step_index=4,
        output='Selected UA123 (cheapest). Proceeding to checkout.',
    )
    # Step 5 (browser): the VISIBLE crash -- the manifestation, not the cause.
    dbg.record_event(
        t, EventType.TOOL_RESULT, agent_name='browser', module='action', step_index=5,
        error='JSON schema validation failed: payment.refund_policy required but missing',
    )
    dbg.finish_trace(t, success=False)
    return dbg, t


def main() -> int:
    dbg, t = build_cascade_trace()

    print('# === Offline heuristic (deterministic, no LLM) ===')
    rule_report = dbg.analyze(t)
    print('root-cause step:', rule_report.root_cause_step_index,
          '| agent:', rule_report.root_cause_agent)
    hyp = HeuristicAttributor().attribute(t, rule_report.findings).hypotheses
    top = hyp[0] if hyp else None
    if top:
        print(f'heuristic blames -> step {top.step_index} ({top.agent_name})')
    print('NOTE: the rule layer keys off the visible error at step 5; the gold '
          'root cause is the planner at step 1. That gap is exactly what the '
          'LLM/attribution layers below are for.\n')

    base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
    api_key = os.environ.get('AGENTDEBUG_LLM_API_KEY')
    if not base_url or not api_key:
        print('Set AGENTDEBUG_LLM_BASE_URL / AGENTDEBUG_LLM_API_KEY to run the '
              'semantic root-cause layers (judge + step-by-step + recovery).')
        return 0

    llm = OpenAICompatClient(
        base_url=base_url, api_key=api_key,
        model=os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash'),
        default_max_tokens=8192, timeout=120.0,
    )

    print('# === LLM judge (semantic) ===')
    judge = LLMJudgeAnalyzer(llm=llm, max_tokens=8192).analyze(t)
    print('root-cause step:', judge.root_cause_step_index,
          '| agent:', judge.root_cause_agent)
    print('summary:', (judge.summary or '')[:160], '\n')

    print('# === Step-by-Step attribution (strongest individual baseline) ===')
    res = StepByStepAttributor(llm=llm, max_steps=10).attribute(t, judge.findings)
    for h in res.hypotheses[:3]:
        print(f'  blame: step {h.step_index} ({h.agent_name}) conf={h.confidence:.2f}')

    print('\n# === Recovery suggestion (suggest-only) ===')
    # Signature is suggest(trajectory, report) -- trajectory first.
    for proposal in ReflexionSuggestion().suggest(t, judge)[:1]:
        print(f'-- {proposal.recoverer_id} --')
        print((proposal.suggestion_text or '')[:240])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
