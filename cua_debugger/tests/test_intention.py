from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from debugger.ingester import IngestionResult, Step
from debugger.memory.intention import build_error_context, extract_intention


def _step(n: int, action: str = "click(100,200)") -> Step:
    return Step(
        step_num=n,
        action_code=action,
        reasoning=f"step {n} reasoning",
        error="",
        reward=0.0,
        done=False,
        action_type="click",
        screenshot_path=Path(f"/tmp/s{n}.png"),
    )


def _make_client(text: str):
    """Build a fake anthropic-shaped client whose messages.create returns ``text``."""
    block = SimpleNamespace(type="text", text=text)
    response = SimpleNamespace(content=[block], stop_reason="end_turn")
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_extract_intention_returns_text():
    ir = IngestionResult(
        status="failure",
        trajectory=[_step(0), _step(1, "type('hello')"), _step(2)],
        terminal_step=2,
        error_msg="",
        task_id="t",
        instruction="open settings",
    )
    ec = build_error_context(ir, error_step=1)

    client = _make_client(
        "What it did: typed 'hello' into the address bar. "
        "What it changed: text appeared in the omnibox."
    )

    out = extract_intention(ec, client=client, model="fake-model", instruction=ir.instruction)
    assert "typed 'hello'" in out
    client.messages.create.assert_called_once()
    # Make sure the prompt actually mentions the action code
    call_kwargs = client.messages.create.call_args.kwargs
    user_msg = call_kwargs["messages"][0]["content"]
    assert "type('hello')" in user_msg
