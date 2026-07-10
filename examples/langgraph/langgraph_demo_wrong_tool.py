"""
Engineered wrong-tool failure
------------------------------

A LangGraph weather agent has only ``get_weather`` but is asked to also send
an alert email when conditions are severe. It calls ``send_alert`` and
``get_forecast`` which don't exist — both return tool-mismatch errors.

AgentDebugX catches ``action.wrong_tool`` and runs heuristic scan, LLM
re-scoring, root-cause attribution, and fix proposals.

Setup::

    pip install 'agentdebugx[langgraph]'
    export AGENTDEBUG_LLM_BASE_URL='https://generativelanguage.googleapis.com/v1beta/openai/'
    export AGENTDEBUG_LLM_API_KEY='AIza...'
    export AGENTDEBUG_LLM_MODEL='gemini-2.0-flash'   # optional

Usage::

    PYTHONPATH=src python examples/langgraph/langgraph_demo_wrong_tool.py
"""

from __future__ import annotations

import os
import sys
from typing import Annotated, TypedDict, cast
from textwrap import dedent

from agentdebug import AgentDebug, SQLiteTraceStore, HeuristicAnalyzer, format_traceback
from agentdebug.adapters.langgraph import LangChainCallbackAdapter
from agentdebug.judges import LLMJudgeAnalyzer
from agentdebug import BinarySearchAttributor, ReflexionSuggestion
from agentdebug.llm import OpenAICompatClient


# ---------------------------------------------------------------------------
# LLM setup — Gemini via OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
api_key  = os.environ.get('AGENTDEBUG_LLM_API_KEY')
model    = os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-2.0-flash')

if not base_url or not api_key:
    print(
        'This demo requires Gemini via the OpenAI-compatible endpoint.\n'
        'Set AGENTDEBUG_LLM_BASE_URL and AGENTDEBUG_LLM_API_KEY.',
        file=sys.stderr,
    )
    raise SystemExit(1)

llm = OpenAICompatClient(
    base_url=base_url, api_key=api_key, model=model,
    default_max_tokens=8192, timeout=180.0,
)
print(f'LLM: {model}')

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.tools import tool
    from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from pydantic import SecretStr
except ImportError as exc:
    print(
        f'This demo needs langchain-openai and langgraph: {exc}\n'
        'Install with: pip install langchain-openai langgraph',
        file=sys.stderr,
    )
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# Tools — get_weather is real; send_alert and get_forecast are decoys.
# The LLM sees all three in the schema but only get_weather is dispatched.
# ---------------------------------------------------------------------------

@tool
def get_weather(city: str) -> str:
    """Get the current weather conditions for a city."""
    data = {
        'san francisco': 'Foggy, 58°F, humidity 85%, wind 12 mph W.',
        'new york':      'Partly cloudy, 91°F, humidity 72%, wind 8 mph SW.',
        'miami':         'Thunderstorms, 95°F, humidity 94%, wind 25 mph SE. Severe weather alert active.',
        'chicago':       'Clear, 34°F, wind chill 22°F, wind 18 mph N.',
    }
    return data.get(city.lower(), f"No weather data available for '{city}'.")


@tool
def send_alert(email: str, message: str) -> str:
    """Send a weather alert email to the given address."""
    ...


@tool
def get_forecast(city: str, days: int) -> str:
    """Get a multi-day weather forecast for a city."""
    ...


TOOLS_BY_NAME = {'get_weather': get_weather}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


llm_agent = ChatOpenAI(
    base_url=base_url,
    api_key=SecretStr(api_key),
    model=model,
).bind_tools([get_weather, send_alert, get_forecast])


def agent_node(state: State) -> dict:
    response = llm_agent.invoke(state['messages'])
    return {'messages': [response]}


def tool_node(state: State) -> dict:
    last = cast(AIMessage, state['messages'][-1])
    results: list[ToolMessage] = []
    for call in last.tool_calls:
        if call['name'] not in TOOLS_BY_NAME:
            content = (
                f"Unknown tool: '{call['name']}' — tool mismatch with available tools. "
                f"Available tools: {list(TOOLS_BY_NAME)}"
            )
            results.append(ToolMessage(content=content, tool_call_id=call['id']))
        else:
            output = TOOLS_BY_NAME[call['name']].invoke(call['args'])
            results.append(ToolMessage(content=str(output), tool_call_id=call['id']))
    return {'messages': results}


def should_continue(state: State) -> str:
    last = state['messages'][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return 'tools'
    return END


builder = StateGraph(State)
builder.add_node('agent', agent_node)
builder.add_node('tools', tool_node)
builder.add_edge(START, 'agent')
builder.add_conditional_edges('agent', should_continue, {'tools': 'tools', END: END})
builder.add_edge('tools', 'agent')
graph = builder.compile()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main() -> int:
    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/langgraph_demo_wrong_tool.sqlite'))
    trajectory = debugger.start_trace(
        goal='Check weather across cities and send alerts for severe conditions',
        framework='langgraph',
    )

    handler = LangChainCallbackAdapter(debugger, trajectory)

    task = dedent("""
        Check the current weather in San Francisco, New York, Miami, and Chicago.
        For any city with severe conditions (storms, extreme heat, or wind chill below 25°F),
        send an alert email to weather-alerts@company.com.
        Also retrieve a 3-day forecast for each city with severe conditions.
    """).strip()

    success = True
    try:
        result = graph.invoke(
            {'messages': [HumanMessage(content=task)]},
            config={'callbacks': [handler]},
        )
        print('\n--- Agent result ---')
        print(result['messages'][-1].content)
    except Exception as exc:
        print(f'Graph invocation failed: {exc}', file=sys.stderr)
        success = False
    finally:
        debugger.finish_trace(trajectory, success=success)

    print('\n--- Stage 1: HeuristicAnalyzer ---')
    report = HeuristicAnalyzer().analyze(trajectory)
    print(report.summary)

    print('\n--- Stage 2: AgentTraceback ---')
    print(format_traceback(report, trajectory, use_color=False))

    print('\n--- Stage 3: LLMJudgeAnalyzer ---')
    report = LLMJudgeAnalyzer(llm=llm, max_tokens=6144).analyze(trajectory)
    print(report.summary)

    print('\n--- Stage 4: BinarySearchAttributor ---')
    binary = BinarySearchAttributor(llm=llm).attribute(trajectory, report.findings)
    if binary.hypotheses:
        h = binary.hypotheses[0]
        print(f'Root cause: agent={h.agent_name} step={h.step_index} conf={h.confidence:.2f}')
    else:
        print('(no hypothesis)')

    print('\n--- Stage 5: ReflexionSuggestion ---')
    for proposal in ReflexionSuggestion().suggest(trajectory, report):
        print(f'  · {proposal.summary}')

    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
