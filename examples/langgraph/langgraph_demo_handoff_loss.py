"""
Multi-agent handoff loss: schema field name mismatch
-----------------------------------------------------

A two-node travel booking pipeline:

    Planner  →  Booking agent

The planner searches options and builds a plan dict with
``free_cancellation_required: True``. The booking agent reads
``plan['cancellation_policy']`` — a different key — gets ``None``,
and books both the flight and hotel as non-refundable. The user's
explicit requirement is silently dropped.

AgentDebugX catches ``multiagent.handoff_loss``.

Setup::

    pip install 'agentdebugx[langgraph]'
    export AGENTDEBUG_LLM_BASE_URL='https://generativelanguage.googleapis.com/v1beta/openai/'
    export AGENTDEBUG_LLM_API_KEY='AIza...'
    export AGENTDEBUG_LLM_MODEL='gemini-2.0-flash'   # optional

Usage::

    PYTHONPATH=src python examples/langgraph/langgraph_demo_handoff_loss.py
"""

from __future__ import annotations

import json
import os
import sys
from textwrap import dedent
from typing import Annotated, Optional, TypedDict, cast

from agentdebug import AgentDebug, HeuristicAnalyzer, SQLiteTraceStore, format_traceback
from agentdebug.adapters.langgraph import LangChainCallbackAdapter
from agentdebug.judges import LLMJudgeAnalyzer
from agentdebug.llm import OpenAICompatClient

base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
api_key  = os.environ.get('AGENTDEBUG_LLM_API_KEY')
model    = os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-2.0-flash')

if not base_url or not api_key:
    print('Set AGENTDEBUG_LLM_BASE_URL and AGENTDEBUG_LLM_API_KEY.', file=sys.stderr)
    raise SystemExit(1)

llm_client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=model,
                                default_max_tokens=8192, timeout=180.0)
print(f'LLM: {model}')

try:
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from pydantic import SecretStr
except ImportError as exc:
    print(f'pip install langchain-openai langgraph: {exc}', file=sys.stderr)
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def book_flight(flight_id: str, cancellation_policy: Optional[str] = None) -> str:
    """Book a flight. cancellation_policy should be 'free' or 'non-refundable'."""
    policy = cancellation_policy or 'non-refundable'
    result = f"Flight {flight_id} booked ({policy})."
    if not cancellation_policy:
        result += (
            " Warning: cancellation_policy is missing"
        )
    return result


@tool
def book_hotel(hotel_id: str, cancellation_policy: Optional[str] = None) -> str:
    """Book a hotel. cancellation_policy should be 'free' or 'non-refundable'."""
    policy = cancellation_policy or 'non-refundable'
    result = f"Hotel {hotel_id} booked ({policy})."
    if not cancellation_policy:
        result += (
            " Warning: cancellation_policy is missing"
        )
    return result


BOOKING_TOOLS = {t.name: t for t in [book_flight, book_hotel]}

planner_chat = ChatOpenAI(base_url=base_url, api_key=SecretStr(api_key), model=model)

PLANNER_SYSTEM = dedent("""
    You are a travel planner. Given a user request, output a JSON travel plan with exactly
    these fields — no extra text, no markdown fences:
    {
      "destination": "...",
      "flight_id": "FL-XXXX",
      "hotel_id": "HT-XXXX",
      "dates": "YYYY-MM-DD → YYYY-MM-DD",
      "budget": 0000,
      "free_cancellation_required": true or false
    }
    Set free_cancellation_required based on whether the user asks for free cancellation.
""").strip()


class State(TypedDict):
    request: str
    plan: dict
    messages: Annotated[list[BaseMessage], add_messages]

def planner_node(state: State) -> dict:
    response = planner_chat.invoke([
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=state['request']),
    ])
    raw = response.content
    text = raw if isinstance(raw, str) else cast(dict, raw[0])['text']
    plan = json.loads(text)
    return {
        'plan': plan,
        'messages': [HumanMessage(content=f'[Planner] Plan: {plan}')],
    }


# ---------------------------------------------------------------------------
# Booking node — LLM-backed; reads cancellation_policy (wrong key → None)
# ---------------------------------------------------------------------------

booking_chat = ChatOpenAI(
    base_url=base_url, api_key=SecretStr(api_key), model=model,
).bind_tools(list(BOOKING_TOOLS.values()))


def booking_node(state: State) -> dict:
    plan = state['plan']
    # BUG: planner wrote 'free_cancellation_required'; booking reads 'cancellation_policy'
    cancellation = plan.get('cancellation_policy')

    prompt = dedent(f"""
        Book the following trip:
          Flight: {plan['flight_id']}  Hotel: {plan['hotel_id']}
          Dates:  {plan['dates']}
          Cancellation policy: {cancellation or 'not specified'}

        Use book_flight then book_hotel. Default to non-refundable if policy unspecified.
    """).strip()

    messages: list[BaseMessage] = [
        SystemMessage(content='You are a travel booking agent. Book the trip using the tools.'),
        HumanMessage(content=prompt),
    ]
    new_msgs: list[BaseMessage] = [HumanMessage(content=prompt)]

    for _ in range(6):
        response = booking_chat.invoke(messages)
        messages.append(response)
        new_msgs.append(response)
        if not getattr(response, 'tool_calls', None):
            break
        for call in response.tool_calls:
            fn = BOOKING_TOOLS.get(call['name'])
            content = fn.invoke(call['args']) if fn else f"Unknown tool: {call['name']}"
            tm = ToolMessage(content=str(content), tool_call_id=call['id'])
            messages.append(tm)
            new_msgs.append(tm)

    return {'messages': new_msgs}


# ---------------------------------------------------------------------------
# Graph: planner → booking
# ---------------------------------------------------------------------------

builder = StateGraph(State)
builder.add_node('planner', planner_node)
builder.add_node('booking', booking_node)
builder.add_edge(START, 'planner')
builder.add_edge('planner', 'booking')
builder.add_edge('booking', END)
graph = builder.compile()

USER_REQUEST = (
    'Book a 5-night Paris trip for 2, August 10-15, budget $3,200. '
    'I need free cancellation — plans might change.'
)


def main() -> int:
    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/langgraph_handoff_loss.sqlite'))
    trajectory = debugger.start_trace(goal=USER_REQUEST, framework='langgraph')
    handler = LangChainCallbackAdapter(debugger, trajectory)

    success = True
    try:
        result = graph.invoke(
            {'request': USER_REQUEST, 'plan': {}, 'messages': []},
            config={'callbacks': [handler]},
        )
        print('\n--- Booking result ---')
        final = next((m for m in reversed(result['messages']) if isinstance(m, AIMessage)), None)
        if final:
            print(final.content)
    except Exception as exc:
        print(f'Graph failed: {exc}', file=sys.stderr)
        success = False
    finally:
        debugger.finish_trace(trajectory, success=success)

    print('\n--- Stage 1: HeuristicAnalyzer ---')
    report = HeuristicAnalyzer().analyze(trajectory)
    print(report.summary)

    print('\n--- Stage 2: AgentTraceback ---')
    print(format_traceback(report, trajectory, use_color=False))

    print('\n--- Stage 3: LLMJudgeAnalyzer ---')
    report = LLMJudgeAnalyzer(llm=llm_client, max_tokens=6144).analyze(trajectory)
    print(report.summary)

    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
