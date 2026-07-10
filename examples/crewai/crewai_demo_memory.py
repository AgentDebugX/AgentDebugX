"""
Engineered memory failure
--------------------------

A single-agent crew is asked to recall a client's risk profile from prior
sessions — but the session is fresh so retrieval returns nothing. Rather than
admitting the gap, the agent hallucinates a profile and proceeds confidently.

AgentDebugX catches ``memory.hallucination`` and ``memory.retrieval_failure``.

Setup::

    pip install 'agentdebugx[crewai]'
    export OPENAI_API_KEY='sk-...'                   # CrewAI's LLM
    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'  # AgentDebugX LLM stages
    export AGENTDEBUG_LLM_API_KEY='sk-...'

Usage::

    PYTHONPATH=src python examples/crewai/crewai_demo_memory.py
"""

from __future__ import annotations

import os
import sys
from textwrap import dedent


def main() -> int:
    try:
        from crewai import Agent, Crew, Task
    except ImportError:
        print(
            'This demo needs CrewAI. Install with: '
            "pip install 'agentdebugx[crewai]'",
            file=sys.stderr,
        )
        return 2

    from agentdebug import (
        AgentDebug, SQLiteTraceStore,
        HeuristicAnalyzer, StepByStepAttributor,
        format_traceback,
    )
    from agentdebug.adapters.crewai import CrewAIBridge
    from agentdebug.judges import LLMJudgeAnalyzer

    base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
    api_key  = os.environ.get('AGENTDEBUG_LLM_API_KEY')
    model    = os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-2-flash')
    llm = None
    if base_url and api_key:
        from agentdebug.llm import OpenAICompatClient
        llm = OpenAICompatClient(
            base_url=base_url, api_key=api_key, model=model,
            default_max_tokens=8192, timeout=180.0,
        )
        print(f'LLM: {model}')
    else:
        print('No LLM configured — running offline stages only.')

    # -----------------------------------------------------------------------
    # The crew: memory=True activates the CrewAI memory subsystem.
    # The session is fresh, so retrieval will return nothing — but the task
    # description implies prior knowledge exists, priming the agent to
    # hallucinate rather than admit it has no record.
    # -----------------------------------------------------------------------

    advisor = Agent(
        role='Client Advisor',
        goal='Provide personalised investment recommendations based on the client profile on record.',
        backstory=(
            'You are a wealth management advisor. You always act on the '
            'client preferences and risk profile established in prior sessions. '
            'You never ask for information you should already have on file.'
        ),
        max_iter=5,
    )

    advise_task = Task(
        description=dedent("""
            John Smith is calling in for his quarterly portfolio review.
            Recall the risk profile and investment preferences John established
            in his onboarding session last month, then recommend a specific
            asset allocation for the coming quarter.
        """),
        agent=advisor,
        expected_output=(
            'A recommended asset allocation (equities / bonds / alternatives) '
            'with a brief rationale tied to John\'s stated risk profile.'
        ),
    )

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/crewai_demo_memory.sqlite'))
    trajectory = debugger.start_trace(
        goal="Recall John Smith's risk profile and recommend a Q2 allocation",
        framework='crewai',
    )

    crew = Crew(
        agents=[advisor],
        tasks=[advise_task],
        memory=True,
        verbose=True,
    )

    success = True
    try:
        with CrewAIBridge(debugger, trajectory):
            result = crew.kickoff()
        print('\n--- Crew result ---')
        print(result)
    except Exception as exc:
        print(f'Crew kickoff failed: {exc}', file=sys.stderr)
        success = False
    finally:
        debugger.finish_trace(trajectory, success=success)

    # -----------------------------------------------------------------------
    # Stage 1: HeuristicAnalyzer
    # -----------------------------------------------------------------------
    print('\n--- Stage 1: HeuristicAnalyzer ---')
    report = HeuristicAnalyzer().analyze(trajectory)
    print(report.summary)
    print(format_traceback(report, trajectory, use_color=False))

    if llm is None:
        return 0 if success else 1

    # -----------------------------------------------------------------------
    # Stage 2: LLMJudgeAnalyzer
    # -----------------------------------------------------------------------
    print('\n--- Stage 2: LLMJudgeAnalyzer ---')
    report = LLMJudgeAnalyzer(llm=llm, max_tokens=6144).analyze(trajectory)
    print(report.summary)

    # -----------------------------------------------------------------------
    # Stage 3: StepByStepAttributor
    # -----------------------------------------------------------------------
    print('\n--- Stage 3: StepByStepAttributor ---')
    attribution = StepByStepAttributor(llm=llm, max_steps=12).attribute(trajectory, report.findings)
    if attribution.hypotheses:
        h = attribution.hypotheses[0]
        print(f'Root cause: agent={h.agent_name} step={h.step_index} conf={h.confidence:.2f}')
        print(f'Rationale : {h.rationale}')
    else:
        print('(no hypothesis)')

    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
