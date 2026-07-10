"""
ReAct agent loop for the trajectory debugger.

Provides run_react_loop() — the shared ReAct engine used by both the
general debugger (run_agent) and the RCA engine (run_rca).
"""

import json
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

from .config import load_config
from .tools import TOOLS
from .prompts import SYSTEM_PROMPT
from .dispatch import dispatch_tool


def _block_to_dict(block) -> dict:
    """Serialize an LLM content block (Anthropic Pydantic or plain dataclass)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    if hasattr(block, "__dict__"):
        return {k: v for k, v in vars(block).items()}
    return {"raw": str(block)}


def _serialize_messages(messages: list) -> list[dict]:
    """Walk message history and serialize content blocks for JSON dump."""
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        elif isinstance(content, list):
            out.append({"role": role, "content": [_block_to_dict(b) for b in content]})
        else:
            out.append({"role": role, "content": _block_to_dict(content) if content else None})
    return out


def _compress_old_screenshots(messages: list, keep_recent: int = 1) -> None:
    """In-place: drop image blocks from tool_results older than the last
    `keep_recent` user messages. Keeps the text portion (action_code, reasoning,
    error) so the model still 'remembers' what each step was. Saves ~80% of
    accumulated context after a few turns of step-detail inspection.
    """
    user_msg_idxs = [i for i, m in enumerate(messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), list)]
    if len(user_msg_idxs) <= keep_recent:
        return  # nothing to compress yet
    stale_idxs = user_msg_idxs[:-keep_recent]
    for idx in stale_idxs:
        new_blocks = []
        for block in messages[idx]["content"]:
            block_dict = block if isinstance(block, dict) else _block_to_dict(block)
            if block_dict.get("type") != "tool_result":
                new_blocks.append(block)
                continue
            inner = block_dict.get("content", [])
            if not isinstance(inner, list):
                new_blocks.append(block)
                continue
            # Keep text-typed blocks, drop image-typed blocks
            kept = [b for b in inner
                    if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "text"]
            if not kept:
                kept = [{"type": "text", "text": "[older tool_result — content omitted to save tokens]"}]
            else:
                kept = list(kept) + [{"type": "text", "text": "[screenshots dropped to save tokens — refer to your prior observation notes; call get_step_details again if you need the pixels]"}]
            new_blocks.append({**block_dict, "content": kept})
        messages[idx]["content"] = new_blocks


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def collect_thinking(messages: list) -> list[str]:
    """Extract all thinking block texts from a message history."""
    traces: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            btype = (
                getattr(block, "type", None)
                or (block.get("type") if isinstance(block, dict) else None)
            )
            if btype == "thinking":
                text = (
                    getattr(block, "thinking", None)
                    or (block.get("thinking") if isinstance(block, dict) else "")
                )
                if text:
                    traces.append(text)
    return traces


# ---------------------------------------------------------------------------
# Shared ReAct loop
# ---------------------------------------------------------------------------

def run_react_loop(
    *,
    client: Anthropic,
    model: str,
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
    traj_data: Optional[dict],
    osworld_root: Path,
    thinking_budget: int,
    max_tokens: int,
    max_turns: int,
    verbose: bool = True,
    verbose_prefix: str = "Turn",
    log_path: Optional[Path] = None,
    timeout: int = 600,
    extras: Optional[dict] = None,
    max_retries: int = 3,
) -> tuple[dict, list[str], Optional[dict]]:
    """
    Run a ReAct tool-use loop until the agent calls finish().

    ``timeout`` (seconds, default 10 min) is propagated as a per-request
    timeout to every ``client.messages.create`` call inside the loop.

    ``extras`` is forwarded verbatim to every ``dispatch_tool`` call so the
    new RCA lesson-exploration tools can find their ``LessonMemory`` and
    ``EpisodicMemory`` instances.  Non-RCA callers leave it ``None``.

    ``max_retries`` (default 3) retries each per-turn ``client.messages.create``
    call when the underlying proxy returns a transient error.  The final
    attempt's exception is re-raised so the outer pipeline observes a real
    failure rather than silent loss.

    When ``log_path`` is given, the full conversation (system + tools + every
    request/response/tool-result) is dumped to that path on completion OR
    failure — useful for debugging RCA token blowups.

    Returns:
        (finish_input, thinking_traces, traj_data)
    """
    finish_input: Optional[dict] = None
    error_msg: Optional[str] = None
    try:
        return _run_react_loop_inner(
            client=client, model=model, system_prompt=system_prompt,
            tools=tools, messages=messages, traj_data=traj_data,
            osworld_root=osworld_root, thinking_budget=thinking_budget,
            max_tokens=max_tokens, max_turns=max_turns,
            verbose=verbose, verbose_prefix=verbose_prefix,
            timeout=timeout, extras=extras, max_retries=max_retries,
        )
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        raise
    finally:
        if log_path:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(json.dumps({
                    "model": model,
                    "system": system_prompt,
                    "tools": tools,
                    "messages": _serialize_messages(messages),
                    "error": error_msg,
                }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            except Exception:
                pass  # log dump must never break the run


def _run_react_loop_inner(
    *,
    client: Anthropic, model: str, system_prompt: str, tools: list[dict],
    messages: list[dict], traj_data: Optional[dict], osworld_root: Path,
    thinking_budget: int, max_tokens: int, max_turns: int,
    verbose: bool, verbose_prefix: str, timeout: int = 600,
    extras: Optional[dict] = None, max_retries: int = 3,
) -> tuple[dict, list[str], Optional[dict]]:
    consecutive_end_turns = 0
    max_consecutive_end_turns = 3

    for turn in range(1, max_turns + 1):
        # Compress stale screenshots before each request — keep only the latest
        # tool_result block's images. Older inspections retain their text
        # (action_code, reasoning) but lose the pixels.
        _compress_old_screenshots(messages, keep_recent=1)

        if verbose:
            print(f"[{verbose_prefix} {turn}] {model}...")

        # Per-turn retry — transient 400/5xx/connection errors on this
        # single LLM call should not abort the whole RCA.  The final attempt
        # re-raises so a genuine outage still surfaces to the caller.
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    thinking={"type": "enabled", "budget_tokens": thinking_budget},
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                    timeout=timeout,
                )
                break
            except Exception as exc:  # noqa: BLE001 — every API error is retryable
                if attempt == max_retries:
                    raise
                if verbose:
                    print(f"  (turn {turn} attempt {attempt}/{max_retries} failed: {exc}; retrying)")

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            consecutive_end_turns += 1
            if consecutive_end_turns >= max_consecutive_end_turns:
                raise RuntimeError(
                    f"Agent returned end_turn without calling finish() for "
                    f"{consecutive_end_turns} consecutive turns (at turn {turn}). "
                    "The model keeps replying with plain text instead of emitting "
                    "a tool_use block. Check system prompt or switch to a more "
                    "tool-use-capable model."
                )
            if verbose:
                print(f"  (end_turn without finish() — reminding and retrying)")
            messages.append({
                "role": "user",
                "content": (
                    "You replied with plain text and did not invoke any tool. "
                    "You MUST submit your final answer by calling the `finish` "
                    "tool — a text-only answer will be discarded. Call "
                    "`finish(...)` now with all required fields."
                ),
            })
            continue

        consecutive_end_turns = 0

        tool_results: list[dict] = []
        finish_input: Optional[dict] = None

        for block in response.content:
            if not hasattr(block, "type") or block.type != "tool_use":
                continue

            if verbose:
                print(f"  -> {block.name}({list(block.input.keys())})")

            if block.name == "finish":
                finish_input = dict(block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Report submitted successfully.",
                })
            else:
                content, traj_data = dispatch_tool(
                    block.name, block.input, traj_data, osworld_root,
                    extras=extras,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                })

        messages.append({"role": "user", "content": tool_results})

        if finish_input is not None:
            if verbose:
                print(f"  Agent finished in {turn} turn(s).")
            return finish_input, collect_thinking(messages), traj_data

    raise RuntimeError(f"Agent did not call finish() within {max_turns} turns.")


# ---------------------------------------------------------------------------
# General debugger entry point
# ---------------------------------------------------------------------------

_cfg = load_config()
THINKING_BUDGET = _cfg.agent_thinking_budget
MAX_TOKENS = _cfg.agent_max_tokens
MAX_TURNS = _cfg.agent_max_turns


def run_agent(
    traj_dir: Path,
    model: str,
    client: Anthropic,
    osworld_root: Path,
    verbose: bool = True,
    timeout: int = 600,
) -> dict:
    """
    Run the general debugger agent and return the report dict.

    ``timeout`` (seconds, default 10 min) bounds every LLM call inside the
    ReAct loop.

    The report is the finish() tool input with _thinking_trace appended.
    """
    messages = [
        {
            "role": "user",
            "content": (
                f"Debug the trajectory at: {traj_dir}\n\n"
                "Use the tools to load and analyze it step by step, then call "
                "finish() with your complete structured report."
            ),
        }
    ]

    report, thinking, _ = run_react_loop(
        client=client,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
        traj_data=None,
        osworld_root=osworld_root,
        thinking_budget=THINKING_BUDGET,
        max_tokens=MAX_TOKENS,
        max_turns=MAX_TURNS,
        verbose=verbose,
        timeout=timeout,
    )
    report["_thinking_trace"] = thinking
    return report
