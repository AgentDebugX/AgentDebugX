"""
Engineered tool-call failure + full analysis pipeline
------------------------------------------------------

A two-agent crew (Equity Researcher + Investor Brief Writer) analyses a stock.
The researcher calls ``fetch_market_data`` whose range codes (``D1``, ``M3``,
``Y1``) are not documented — the agent guesses plausible strings and the tool
raises. AgentDebugX catches ``action.parameter_error`` and walks the full
analysis stack: heuristic scan, LLM re-scoring, four attributors, DeepDebug,
and fix proposals.

Setup::

    pip install 'agentdebugx[crewai]'
    export OPENAI_API_KEY='sk-...'                   # CrewAI's LLM
    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'  # AgentDebugX LLM stages
    export AGENTDEBUG_LLM_API_KEY='sk-...'

Usage::

    PYTHONPATH=src python examples/crewai/crewai_demo_tool_call.py
"""

from __future__ import annotations

import os
import sys
from textwrap import dedent


def main() -> int:
    try:
        from crewai import Agent, Crew, Task
        from crewai.tools import tool
    except ImportError:
        print(
            'This demo needs CrewAI. Install with: '
            "pip install 'agentdebugx[crewai]'",
            file=sys.stderr,
        )
        return 2

    from agentdebug import (
        AgentDebug, SQLiteTraceStore,
        HeuristicAnalyzer,
        HeuristicAttributor, AllAtOnceAttributor,
        StepByStepAttributor, BinarySearchAttributor,
        ReflexionSuggestion, CriticRecoverer,
        format_traceback,
    )
    from agentdebug.adapters.crewai import CrewAIBridge
    from agentdebug.deep import DeepDebugAnalyzer
    from agentdebug.judges import LLMJudgeAnalyzer

    # -----------------------------------------------------------------------
    # Optional LLM — stages 3-6 are skipped when credentials are absent
    # -----------------------------------------------------------------------
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
    # Build the crew with an engineered tool failure
    # -----------------------------------------------------------------------

    @tool('Fetch market data')
    def fetch_market_data(ticker: str, range_code: str) -> str:
        """Fetch historical market data for a ticker over a range.

        Args:
            ticker: stock symbol (e.g. ``AAPL``).
            range_code: proprietary range token.
        """
        valid = {'D1', 'W1', 'M1', 'M3', 'Y1'}
        if range_code not in valid:
            raise ValueError(
                f'invalid argument range_code={range_code!r}; '
            )
        return (
            f'{ticker} over {range_code}: closing prices stable, '
            f'volume within expected band, no anomalous events.'
        )

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/crewai_demo_v2.sqlite'))
    trajectory = debugger.start_trace(
        goal='Investigate AAPL over the last quarter and draft an investor brief',
        framework='crewai',
    )

    researcher = Agent(
        role='Equity Researcher',
        goal='Pull market data for the requested ticker and judge whether the company looks healthy.',
        backstory='You are a buy-side analyst who queries an internal market-data API to inform investor briefs.',
        max_iter=5,
        tools=[fetch_market_data],
    )
    writer = Agent(
        role='Investor Brief Writer',
        goal="Translate the researcher's findings into a concise memo.",
        backstory='You distill analyst notes into client-ready prose.',
        max_iter=5,
    )

    research_task = Task(
        description=dedent("""
            Investigate AAPL over the past 3 months. Use fetch_market_data to
            retrieve the data, then judge whether the stock looks healthy.
        """),
        agent=researcher,
        expected_output='3 bullet points summarising the data.',
    )
    writer_task = Task(
        description=dedent("""
            Using the researcher's findings, write a 3-sentence investor
            brief for a non-technical reader.
        """),
        agent=writer,
        expected_output='A 3-sentence investor brief.',
        context=[research_task],
    )

    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, writer_task],
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
    # Stage 1: HeuristicAnalyzer — deterministic rule scan, always available
    # -----------------------------------------------------------------------
    print('\n--- Stage 1: HeuristicAnalyzer ---')
    report = HeuristicAnalyzer().analyze(trajectory)
    print(report.summary)

    # -----------------------------------------------------------------------
    # Stage 2: AgentTraceback — cascade render of the rule findings
    # -----------------------------------------------------------------------
    print('\n--- Stage 2: AgentTraceback ---')
    print(format_traceback(report, trajectory, use_color=False))

    if llm is None:
        return 0 if success else 1

    # -----------------------------------------------------------------------
    # Stage 3: LLMJudgeAnalyzer — LLM re-scores and extends the findings
    # -----------------------------------------------------------------------
    print('\n--- Stage 3: LLMJudgeAnalyzer ---')
    report = LLMJudgeAnalyzer(llm=llm, max_tokens=6144).analyze(trajectory)
    print(report.summary)

    # -----------------------------------------------------------------------
    # Stage 4: Attributors — four strategies to pinpoint root-cause agent/step
    # -----------------------------------------------------------------------
    print('\n--- Stage 4: Attributors ---')

    heuristic = HeuristicAttributor().attribute(trajectory, report.findings)
    print(f'Heuristic    → {heuristic.hypotheses[0].agent_name} '
          f'step={heuristic.hypotheses[0].step_index}' if heuristic.hypotheses else 'Heuristic    → (no hypothesis)')

    all_at_once = AllAtOnceAttributor(llm=llm, max_tokens=2048).attribute(trajectory, report.findings)
    print(f'AllAtOnce    → {all_at_once.hypotheses[0].agent_name} '
          f'step={all_at_once.hypotheses[0].step_index}' if all_at_once.hypotheses else 'AllAtOnce    → (no hypothesis)')

    step_by_step = StepByStepAttributor(llm=llm, max_steps=12).attribute(trajectory, report.findings)
    print(f'StepByStep   → {step_by_step.hypotheses[0].agent_name} '
          f'step={step_by_step.hypotheses[0].step_index}' if step_by_step.hypotheses else 'StepByStep   → (no hypothesis)')

    binary = BinarySearchAttributor(llm=llm).attribute(trajectory, report.findings)
    print(f'BinarySearch → {binary.hypotheses[0].agent_name} '
          f'step={binary.hypotheses[0].step_index}' if binary.hypotheses else 'BinarySearch → (no hypothesis)')

    # -----------------------------------------------------------------------
    # Stage 5: DeepDebugAnalyzer — multi-round plan→hypothesize→verify→refine
    # -----------------------------------------------------------------------
    print('\n--- Stage 5: DeepDebugAnalyzer ---')
    deep = DeepDebugAnalyzer(
        llm=llm,
        max_focus_events=5,
        max_hypotheses_to_verify=3,
        max_tokens=6144,
    ).analyze(trajectory)
    print(deep.report.summary)
    print(format_traceback(deep.report, trajectory, use_color=False))

    # -----------------------------------------------------------------------
    # Stage 6: ReflexionSuggestion + CriticRecoverer — concrete fix proposals
    # -----------------------------------------------------------------------
    print('\n--- Stage 6: ReflexionSuggestion ---')
    for proposal in ReflexionSuggestion().suggest(trajectory, report):
        print(f'  · {proposal.summary}')

    print('\n--- Stage 7: CriticRecoverer ---')
    for proposal in CriticRecoverer().suggest(trajectory, report):
        print(f'  · {proposal.summary}')

    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
