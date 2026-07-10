"""End-to-end demo: rule analyzer + LLM judge + attributor + Reflexion suggestion.

Set the env vars before running:

    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'
    export AGENTDEBUG_LLM_API_KEY='sk-...'
    export AGENTDEBUG_LLM_MODEL='gemini-3-flash'   # optional, defaults below

Then::

    PYTHONPATH=src python examples/llm_judge_demo.py
"""

from __future__ import annotations

import os
import sys

from agentdebug import AgentDebug, EventType
from agentdebug.analyzers import HeuristicAnalyzer
from agentdebug.attribution import AllAtOnceAttributor
from agentdebug.judges import LLMJudgeAnalyzer
from agentdebug.llm import OpenAICompatClient
from agentdebug.recovery import ReflexionSuggestion


def build_failing_trace() -> tuple[AgentDebug, object]:
    debugger = AgentDebug()
    trajectory = debugger.start_trace(
        goal='Find the latest AgentDebug paper and summarize the method',
        framework='llm_judge_demo',
    )
    debugger.record_event(
        trajectory,
        EventType.PLAN,
        agent_name='planner',
        module='planning',
        step_index=1,
        output='Search the web with query="latest AgentDebug paper", then summarize.',
    )
    debugger.record_event(
        trajectory,
        EventType.TOOL_CALL,
        agent_name='search_web',
        module='action',
        step_index=2,
        input={'tool': 'search_web', 'args': {}},
    )
    debugger.record_event(
        trajectory,
        EventType.TOOL_RESULT,
        agent_name='search_web',
        module='action',
        step_index=3,
        error='JSON schema validation failed: missing parameter query',
    )
    debugger.record_event(
        trajectory,
        EventType.OBSERVATION,
        agent_name='planner',
        module='reflection',
        step_index=4,
        output='Final answer: AgentDebug is a popular paper.',
    )
    debugger.finish_trace(trajectory, success=False)
    return debugger, trajectory


def main() -> int:
    base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
    api_key = os.environ.get('AGENTDEBUG_LLM_API_KEY')
    model = os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash')
    if not base_url or not api_key:
        print(
            'Set AGENTDEBUG_LLM_BASE_URL and AGENTDEBUG_LLM_API_KEY before running.',
            file=sys.stderr,
        )
        return 2

    debugger, trajectory = build_failing_trace()

    print('# === Rule-based baseline ===')
    rule_report = HeuristicAnalyzer().analyze(trajectory)
    for f in rule_report.findings:
        print(f'  - {f.failure_mode.mode_id} (conf={f.confidence:.2f}) at step={f.step_index}')

    llm = OpenAICompatClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        default_max_tokens=8192,
        timeout=120.0,
    )

    print('\n# === LLM judge ===')
    judge_report = LLMJudgeAnalyzer(llm=llm, max_tokens=8192).analyze(trajectory)
    print('summary:', judge_report.summary)
    for f in judge_report.findings:
        print(f'  - {f.failure_mode.mode_id} (conf={f.confidence:.2f}) at step={f.step_index}')

    print('\n# === All-at-Once attribution ===')
    result = AllAtOnceAttributor(llm=llm, max_tokens=4096).attribute(
        trajectory, judge_report.findings
    )
    print('method:', result.method)
    for h in result.hypotheses:
        print(
            f'  blame: agent={h.agent_name} step={h.step_index} '
            f'confidence={h.confidence:.2f}'
        )
        print(f'         rationale: {h.rationale}')

    print('\n# === Reflexion suggestions ===')
    for proposal in ReflexionSuggestion().suggest(trajectory, judge_report):
        print(f'-- {proposal.recoverer_id} ({proposal.proposal_id}) --')
        print(proposal.suggestion_text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
