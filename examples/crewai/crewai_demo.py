"""End-to-end demo: record a CrewAI Crew run with AgentDebug.

Requirements:

    pip install 'agentdebugx[crewai]'
    export OPENAI_API_KEY=...     # whatever CrewAI's LLM provider needs

This is meant to be copy-pasteable. Run it; inspect the trace at
``.agentdebug/crewai_demo.sqlite`` or launch the dashboard::

    agentdebug serve --store-sqlite .agentdebug/crewai_demo.sqlite

The pattern is intentionally tiny: wrap the kickoff in a CrewAIBridge
context manager and every Crew / Task / Agent / LLM / Tool event lands
in your AgentDebug trajectory automatically — no callbacks, no listeners
to register, no monkey-patching of CrewAI's runtime.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from crewai import Agent, Crew, Task
    except ImportError:
        print(
            "This demo needs CrewAI. Install with: "
            "pip install 'agentdebugx[crewai]'",
            file=sys.stderr,
        )
        return 2

    from agentdebug import AgentDebug, SQLiteTraceStore
    from agentdebug.adapters.crewai import CrewAIBridge
    from agentdebug.analyzers import HeuristicAnalyzer
    from agentdebug.traceback import format_traceback

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/crewai_demo.sqlite'))
    trajectory = debugger.start_trace(
        goal='Research the top 3 trends in agent debugging',
        framework='crewai',
    )

    researcher = Agent(
        role='Senior Researcher',
        goal='Identify trends in agent observability',
        backstory='You have read every AgentDebug-related paper.',
    )
    summarizer = Agent(
        role='Editor',
        goal='Produce a one-paragraph executive summary',
        backstory='You compress dense research into clear prose.',
    )

    research_task = Task(
        description='Find the top 3 trends in LLM agent debugging.',
        agent=researcher,
        expected_output='A bulleted list of 3 trends.',
    )
    summary_task = Task(
        description='Summarize the research findings in one paragraph.',
        agent=summarizer,
        expected_output='A single concise paragraph.',
        context=[research_task],
    )
    crew = Crew(agents=[researcher, summarizer], tasks=[research_task, summary_task])

    success = True
    try:
        with CrewAIBridge(debugger, trajectory):
            result = crew.kickoff()
        print('\n--- Crew result ---')
        print(result)
    except Exception as exc:
        print(f'crew kickoff failed: {exc}', file=sys.stderr)
        success = False
    finally:
        debugger.finish_trace(trajectory, success=success)

    print('\n--- Diagnostic report ---')
    report = HeuristicAnalyzer().analyze(trajectory)
    print(report.summary)

    print('\n--- AgentTraceback ---')
    print(format_traceback(report, trajectory, use_color=False))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
