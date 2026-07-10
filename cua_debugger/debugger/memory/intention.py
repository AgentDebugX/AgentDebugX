"""
Step 1 — Error Context (EC_t) construction and Agent Intention extraction.

EC_t is a window of [t-1, t, t+1] around the root error step. The intention
extractor takes EC_t and produces a 1-3 sentence "what it did / what it changed"
summary, adapted from MGA's 5-dimension memory structure.
"""

from __future__ import annotations

from typing import Any

from debugger.ingester import IngestionResult, Step
from debugger.prompts import INTENTION_EXTRACTION_PROMPT


def _step_to_dict(s: Step) -> dict[str, Any]:
    return {
        "step_num": s.step_num,
        "action_code": s.action_code,
        "reasoning": s.reasoning,
        "error": s.error,
        "reward": s.reward,
        "done": s.done,
        "action_type": s.action_type,
        "screenshot_path": str(s.screenshot_path) if s.screenshot_path else None,
    }


def build_error_context(ir: IngestionResult, error_step: int) -> dict[str, Any]:
    """
    Slice the EC_t window [t-1, t, t+1] around ``error_step``.

    Returns a dict::

        {
            "error_step": int,
            "window":     [start, end],   # inclusive, after clipping
            "steps":      [<step dict>, ...],
        }
    """
    steps = ir.trajectory
    if not steps:
        return {"error_step": error_step, "window": [error_step, error_step], "steps": []}

    by_num = {s.step_num: s for s in steps}
    candidates = [error_step - 1, error_step, error_step + 1]
    selected = [by_num[n] for n in candidates if n in by_num]

    if not selected:
        # error_step out of range — fall back to nearest available
        nearest = min(steps, key=lambda s: abs(s.step_num - error_step))
        selected = [nearest]

    return {
        "error_step": error_step,
        "window": [selected[0].step_num, selected[-1].step_num],
        "steps": [_step_to_dict(s) for s in selected],
    }


def _format_ec_for_prompt(ec: dict[str, Any], instruction: str) -> str:
    lines = [f"Task instruction: {instruction}", "", f"Error step: {ec['error_step']}", ""]
    for s in ec["steps"]:
        marker = "  <-- error step" if s["step_num"] == ec["error_step"] else ""
        lines.append(f"--- Step {s['step_num']}{marker} ---")
        lines.append(f"action_code: {s['action_code']}")
        if s.get("reasoning"):
            lines.append(f"reasoning: {s['reasoning']}")
        if s.get("error"):
            lines.append(f"error: {s['error']}")
        lines.append("")
    return "\n".join(lines)


def extract_intention(
    ec: dict[str, Any],
    client,
    model: str,
    instruction: str = "",
    max_tokens: int = 400,
) -> str:
    """
    Single LLM call → 1-3 sentence intention summary for EC_t.

    Returns the summary text. The caller is responsible for storing it.
    """
    user_content = _format_ec_for_prompt(ec, instruction)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=INTENTION_EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()
