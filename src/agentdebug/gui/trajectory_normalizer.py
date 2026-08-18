"""
In-memory trajectory coordinate normalizer.

Rewrites pyautogui commands in Anthropic agent trajectories to use
model-scale coordinates (from action.input.coordinate) instead of
screen-scale coordinates.  The original trajectory dict is not mutated.

Other agent formats pass through unchanged.
"""

import copy
import re


# Regex: standard pyautogui calls where coordinates are args 1 & 2.
# Matches up to the two coordinate integers, leaving trailing args intact.
_STANDARD_RE = re.compile(
    r'pyautogui\.'
    r'(click|doubleClick|rightClick|middleClick|tripleClick'
    r'|moveTo|dragTo|mouseDown|mouseUp)'
    r'\(\s*\d+\s*,\s*\d+'
)

# Regex: scroll/hscroll where coordinates are args 2 & 3 (amount is arg 1).
_SCROLL_RE = re.compile(
    r'pyautogui\.(scroll|hscroll)\(\s*(-?\d+)\s*,\s*\d+\s*,\s*\d+'
)


def _normalize_step(action_code: str, action_input: dict) -> str:
    """Rewrite coordinate values in a single step's action_code."""
    coord = action_input.get("coordinate")
    if not coord or not isinstance(coord, (list, tuple)) or len(coord) < 2:
        return action_code

    start = action_input.get("start_coordinate")

    # Special case: left_click_drag → moveTo(start) + dragTo(coord)
    if start is not None and isinstance(start, (list, tuple)) and len(start) >= 2:
        action_code = re.sub(
            r'pyautogui\.moveTo\(\s*\d+\s*,\s*\d+',
            f'pyautogui.moveTo({start[0]}, {start[1]}',
            action_code, count=1,
        )
        action_code = re.sub(
            r'pyautogui\.dragTo\(\s*\d+\s*,\s*\d+',
            f'pyautogui.dragTo({coord[0]}, {coord[1]}',
            action_code, count=1,
        )
        return action_code

    # Standard calls: replace first two numeric args with model coords
    action_code = _STANDARD_RE.sub(
        lambda m: f'pyautogui.{m.group(1)}({coord[0]}, {coord[1]}',
        action_code,
    )

    # Scroll/hscroll: replace args 2 & 3, keep amount (arg 1)
    action_code = _SCROLL_RE.sub(
        lambda m: f'pyautogui.{m.group(1)}({m.group(2)}, {coord[0]}, {coord[1]}',
        action_code,
    )

    return action_code


def normalize_trajectory(traj_dict: dict) -> dict:
    """
    Return a deep copy of *traj_dict* with Anthropic action_code strings
    rewritten to use model-scale coordinates.

    Non-Claude formats and steps without action_input.coordinate pass
    through unchanged.
    """
    if traj_dict.get("format") != "claude":
        return copy.deepcopy(traj_dict)

    result = copy.deepcopy(traj_dict)
    for step in result["steps"]:
        ai = step.get("action_input", {})
        if (
            isinstance(ai, dict)
            and "coordinate" in ai
            and step.get("action_type") == "tool_use"
        ):
            step["action_code"] = _normalize_step(step["action_code"], ai)
    return result
