"""Minimal AgentDebugX example."""

from agentdebug import AgentDebug, EventType, SQLiteTraceStore
from agentdebug.models import model_to_json


def main() -> None:
    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/example.sqlite'))

    with debugger.trace(
        goal='Summarize the latest AgentDebug paper',
        framework='custom-react-agent',
    ) as trace:
        trace.record(
            EventType.PLAN,
            agent_name='planner',
            module='planning',
            step_index=1,
            output='Search the web, read the paper, summarize the method.',
        )
        trace.record(
            EventType.TOOL_RESULT,
            agent_name='search',
            module='action',
            step_index=2,
            error='JSON schema validation failed: missing parameter query',
        )
        report = trace.analyze()

    print(model_to_json(report, indent=2))


if __name__ == '__main__':
    main()
