"""
Tool dispatch for the trajectory debugger ReAct agent.

Each tool call from Claude is routed here. Returns a list of Anthropic
content blocks (text and/or image) suitable for a tool_result message.
"""

from pathlib import Path
from typing import Optional

from .trajectory import load_normalized_trajectory, find_step, image_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_previous_step(step_num: int, traj_data: dict) -> Optional[dict]:
    """Return the step immediately before *step_num* in the sorted step list."""
    prev = None
    for s in traj_data["steps"]:
        if s["step_num"] == step_num:
            return prev
        prev = s
    return None


def _find_initial_screenshot(traj_data: dict) -> Optional[Path]:
    """Probe for an initial-state screenshot on disk (before step 1)."""
    traj_dir = Path(traj_data.get("traj_dir", ""))
    if not traj_dir.is_dir():
        return None
    for name in ("step_0.png", "initial_state.png"):
        p = traj_dir / name
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Formatters (tool result text)
# ---------------------------------------------------------------------------

def format_traj_index(traj_data: dict) -> str:
    """Human-readable step index returned by load_trajectory."""
    lines = [
        f"Task: {traj_data['instruction'] or '(not available)'}",
        f"Result score: {traj_data['result_score'] if traj_data['result_score'] is not None else 'unknown'}",
        f"Total steps: {len(traj_data['steps'])}",
        "",
        "Step index:",
        f"  {'Step':>4}  {'Action type':<12}  {'Error':6}  Screenshot",
    ]
    for s in traj_data["steps"]:
        err_flag = "ERROR" if s["error"] else "ok"
        ss_flag = "yes" if s["screenshot_path"] else "no"
        lines.append(
            f"  {s['step_num']:>4}  {s['action_type']:<12}  {err_flag:<6}  {ss_flag}"
        )
    if traj_data["system_errors"]:
        lines += ["", "System-level errors (not tied to a step):"]
        for e in traj_data["system_errors"]:
            lines.append(f"  {e}")
    return "\n".join(lines)


def format_step_detail(step: dict) -> str:
    """Detailed text for a single step."""
    lines = [
        f"Step {step['step_num']}",
        f"Action type: {step['action_type']}",
        f"Action code:\n{step['action_code']}",
        f"Execution error: {step['error'] or 'none'}",
        f"Reward: {step['reward']}  |  Done: {step['done']}",
    ]
    if step["reasoning"]:
        lines += ["", f"Agent reasoning:\n{step['reasoning']}"]
    if step.get("llm_tool_use"):
        lines += ["", f"LLM tool use:\n{step['llm_tool_use']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(
    name: str,
    tool_input: dict,
    traj_data: Optional[dict],
    osworld_root: Path,
    extras: Optional[dict] = None,
) -> tuple[list[dict], Optional[dict]]:
    """
    Execute a named tool and return (content_blocks, updated_traj_data).

    content_blocks is passed directly as the tool_result content list.
    traj_data is updated in-place when load_trajectory is called.

    ``extras`` is an optional dict carrying runtime instances that the new
    RCA-context lesson tools need but the descriptor layer cannot supply
    (``lesson_memory``, ``episodic_memory``, ``lesson_injector_exclude_ids``).
    Non-RCA callers may safely leave it ``None`` — those branches simply
    return a structured error if they happen to be triggered with no extras.
    """
    extras = extras or {}

    def text(t: str) -> dict:
        return {"type": "text", "text": t}

    # ---- load_trajectory ----
    if name == "load_trajectory":
        # If traj_data is already loaded (e.g. pre-loaded by RCA engine),
        # return the existing data instead of hitting disk with a potentially
        # hallucinated path from the model.
        if traj_data is not None:
            return [text(format_traj_index(traj_data))], traj_data
        raw_path = tool_input["path"]
        p = Path(raw_path)
        if not p.is_absolute():
            # try relative to cwd, then to OSWorld root
            if p.exists():
                p = p.resolve()
            else:
                alt = osworld_root / p
                p = alt.resolve() if alt.exists() else p.resolve()
        traj_data = load_normalized_trajectory(p)
        return [text(format_traj_index(traj_data))], traj_data

    # guard: other tools require traj_data
    if traj_data is None:
        return [text("ERROR: call load_trajectory before using other tools.")], traj_data

    # ---- get_step_details (combined text + input/result screenshots) ----
    if name == "get_step_details":
        step = find_step(tool_input["step_num"], traj_data)
        if step is None:
            return [text(f"Step {tool_input['step_num']} not found in trajectory.")], traj_data

        content: list[dict] = [text(format_step_detail(step))]

        # Input screenshot: what the agent saw BEFORE deciding on this action
        prev_step = _find_previous_step(tool_input["step_num"], traj_data)
        if prev_step is not None:
            input_img = image_block(prev_step["screenshot_path"])
            if input_img:
                content.append(text(f"Input screenshot (step {prev_step['step_num']} result — the screen state the agent saw before choosing this action):"))
                content.append(input_img)
            else:
                content.append(text(f"(input screenshot from step {prev_step['step_num']} not available)"))
        else:
            # First step — try to find initial state screenshot on disk
            initial_path = _find_initial_screenshot(traj_data)
            if initial_path:
                input_img = image_block(initial_path)
                if input_img:
                    content.append(text("Input screenshot (initial state — screen before any action):"))
                    content.append(input_img)
                else:
                    content.append(text("(initial state screenshot exists but could not be loaded)"))
            else:
                content.append(text("(no input screenshot — this is the first step and no initial state was saved)"))

        # Result screenshot: screen state AFTER this action executed
        result_img = image_block(step["screenshot_path"])
        if result_img:
            content.append(text(f"Result screenshot (step {step['step_num']} — screen state after action executed):"))
            content.append(result_img)
        else:
            content.append(text("(no result screenshot available for this step)"))

        return content, traj_data

    # ---- propose_annotation (discussion-only) ----
    if name == "propose_annotation":
        return [text("Annotation proposal submitted. The human can now review and apply it.")], traj_data

    # ---- lookup_lessons_by_taxonomy (rca-only) ----
    if name == "lookup_lessons_by_taxonomy":
        from debugger.tools.lesson_explorer import lookup_lessons_by_taxonomy

        lesson_memory = extras.get("lesson_memory")
        if lesson_memory is None:
            return [text("ERROR: lesson_memory not available in this run.")], traj_data
        result_text = lookup_lessons_by_taxonomy(
            lesson_memory=lesson_memory,
            taxonomy_tag=tool_input["taxonomy_tag"],
            top_k=int(tool_input.get("top_k", 3)),
            exclude_lesson_ids=extras.get("table_representative_ids"),
        )
        return [text(result_text)], traj_data

    # ---- search_lessons_by_app (rca-only) ----
    if name == "search_lessons_by_app":
        from debugger.tools.lesson_explorer import search_lessons_by_app

        lesson_memory = extras.get("lesson_memory")
        if lesson_memory is None:
            return [text("ERROR: lesson_memory not available in this run.")], traj_data
        result_text = search_lessons_by_app(
            lesson_memory=lesson_memory,
            app_id=tool_input["app_id"],
            taxonomy_tag=tool_input.get("taxonomy_tag"),
            top_k=int(tool_input.get("top_k", 3)),
        )
        return [text(result_text)], traj_data

    # ---- follow_episodic_ref (rca-only) ----
    if name == "follow_episodic_ref":
        from debugger.tools.lesson_explorer import follow_episodic_ref

        episodic_memory = extras.get("episodic_memory")
        if episodic_memory is None:
            return [text("ERROR: episodic_memory not available in this run.")], traj_data
        result_text = follow_episodic_ref(
            episodic_memory=episodic_memory,
            episodic_ref=tool_input["episodic_ref"],
        )
        return [text(result_text)], traj_data

    return [text(f"Unknown tool: {name}")], traj_data
