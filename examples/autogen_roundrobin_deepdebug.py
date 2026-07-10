import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.tools import AgentTool
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelFamily

from agentdebug import AgentDebug, SQLiteTraceStore
from agentdebug.deep import DeepDebugAnalyzer
from agentdebug.llm import OpenAICompatClient
from agentdebug.models import model_to_json


async def _run_and_record(task: str, agent: AssistantAgent, debugger: AgentDebug) -> None:
    with debugger.trace(goal=task, framework='autogen-agenttool') as t:
        step = 0
        stream = agent.run_stream(task=task)
        async for msg in stream:
            step += 1
            text = str(getattr(msg, 'content', None) or msg)
            name = str(
                getattr(msg, 'source', None)
                or getattr(msg, 'name', None)
                or getattr(msg, 'agent', None)
                or 'agent'
            )
            print(f'[{step:02d}] {name}: {text}')
            t.record(
                agent_name=name,
                module='autogen-agenttool',
                step_index=step,
                output=text,
            )
        trajectory = t.trajectory

    llm = OpenAICompatClient(
        base_url=os.environ['AGENTDEBUG_LLM_BASE_URL'],
        api_key=os.environ['AGENTDEBUG_LLM_API_KEY'],
        model=os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash'),
        default_max_tokens=8192,
        timeout=180.0,
    )
    result = DeepDebugAnalyzer(llm=llm).analyze(trajectory)
    print('\n=== DeepDebug Summary ===')
    print(result.report.summary)
    print('root_cause_agent =', result.report.root_cause_agent)
    print('root_cause_step_index =', result.report.root_cause_step_index)
    print('\n=== Full Report JSON ===')
    print(model_to_json(result.report, indent=2))


async def main() -> None:
    base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
    api_key = os.environ.get('AGENTDEBUG_LLM_API_KEY')
    if not base_url or not api_key:
        raise RuntimeError('Set AGENTDEBUG_LLM_BASE_URL and AGENTDEBUG_LLM_API_KEY first.')

    model_name = os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash')

    # AutoGen runtime client (same endpoint/key/model as DeepDebug path)
    model_client = OpenAIChatCompletionClient(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        model_info={
            'vision': False,
            'function_calling': True,
            'json_output': True,
            'structured_output': True,
            'family': ModelFamily.GEMINI_2_5_FLASH,
        },
    )

    math_agent = AssistantAgent(
        'math_expert',
        model_client=model_client,
        system_message='You are a math expert.',
        description='A math expert assistant.',
        model_client_stream=True,
    )
    math_agent_tool = AgentTool(math_agent, return_value_as_last_message=True)

    chemistry_agent = AssistantAgent(
        'chemistry_expert',
        model_client=model_client,
        system_message='You are a chemistry expert.',
        description='A chemistry expert assistant.',
        model_client_stream=True,
    )
    chemistry_agent_tool = AgentTool(chemistry_agent, return_value_as_last_message=True)

    agent = AssistantAgent(
        'assistant',
        system_message='You are a general assistant. Use expert tools when needed.',
        model_client=model_client,
        model_client_stream=True,
        tools=[math_agent_tool, chemistry_agent_tool],
        max_tool_iterations=5,
    )

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/autogen_roundrobin.sqlite'))
    await _run_and_record('What is the integral of x^2?', agent, debugger)


if __name__ == '__main__':
    asyncio.run(main())
