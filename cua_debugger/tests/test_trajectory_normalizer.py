# tests/test_trajectory_normalizer.py
import pytest
from debugger.trajectory import load_trajectory

def _make_claude_traj(tmp_path, steps):
    """Helper: write a minimal traj.jsonl from a list of step dicts."""
    import json
    traj_dir = tmp_path / "task-abc"
    traj_dir.mkdir()
    with open(traj_dir / "traj.jsonl", "w") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")
    # result.txt so loader doesn't complain
    (traj_dir / "result.txt").write_text("0.0")
    return traj_dir


def test_load_trajectory_preserves_action_input(tmp_path):
    """load_trajectory() should include action_input with the raw action.input dict."""
    traj_dir = _make_claude_traj(tmp_path, [
        {
            "step_num": 1,
            "action": {
                "action_type": "tool_use",
                "command": "pyautogui.click(1890, 139)\n",
                "raw_response": "[TEXT] clicking",
                "input": {"action": "left_click", "coordinate": [1260, 93]},
                "name": "computer",
            },
            "reward": 0,
            "done": False,
            "info": {},
            "screenshot_file": "",
        }
    ])
    traj = load_trajectory(traj_dir)
    step = traj["steps"][0]
    assert "action_input" in step
    assert step["action_input"]["coordinate"] == [1260, 93]
    assert step["action_input"]["action"] == "left_click"


def test_load_trajectory_legacy_has_empty_action_input(tmp_path):
    """Legacy-format steps should have action_input as empty dict."""
    import json
    traj_dir = tmp_path / "task-legacy"
    traj_dir.mkdir()
    with open(traj_dir / "trajectory.jsonl", "w") as f:
        f.write(json.dumps({
            "step_num": 1,
            "action": "pyautogui.click(100, 200)\n",
            "reward": 0,
            "done": False,
            "info": {},
            "action_timestamp": "20251030@070921",
        }) + "\n")
    (traj_dir / "result.txt").write_text("0.0")
    traj = load_trajectory(traj_dir)
    step = traj["steps"][0]
    assert step["action_input"] == {}


# ---------------------------------------------------------------------------
# Task 2: normalize_trajectory tests
# ---------------------------------------------------------------------------

from debugger.trajectory_normalizer import normalize_trajectory


def _make_traj_dict(steps, fmt="claude"):
    """Helper: build an in-memory traj_dict (no disk needed)."""
    return {
        "task_id": "test-task",
        "instruction": "do something",
        "result_score": 0.0,
        "traj_dir": "/tmp/fake",
        "format": fmt,
        "steps": steps,
        "system_errors": [],
    }


def _make_step(step_num, action_code, action_input, action_type="tool_use"):
    return {
        "step_num": step_num,
        "action_code": action_code,
        "reasoning": "[TEXT] reasoning",
        "error": "",
        "reward": 0,
        "done": False,
        "action_type": action_type,
        "screenshot_path": None,
        "action_input": action_input,
    }


def test_normalize_standard_click():
    """pyautogui.click screen coords should be replaced with model coords."""
    step = _make_step(
        1,
        "pyautogui.click(1890, 139)\n",
        {"action": "left_click", "coordinate": [1260, 93]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.click(1260, 93)" in result["steps"][0]["action_code"]
    # Original should be untouched
    assert "pyautogui.click(1890, 139)" in traj["steps"][0]["action_code"]


def test_normalize_double_click():
    step = _make_step(
        1,
        "pyautogui.doubleClick(300, 400)\n",
        {"action": "double_click", "coordinate": [200, 267]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.doubleClick(200, 267)" in result["steps"][0]["action_code"]


def test_normalize_right_click():
    step = _make_step(
        1,
        "pyautogui.rightClick(900, 600)\n",
        {"action": "right_click", "coordinate": [600, 400]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.rightClick(600, 400)" in result["steps"][0]["action_code"]


# ---------------------------------------------------------------------------
# Task 3: edge case tests
# ---------------------------------------------------------------------------


def test_normalize_left_click_drag():
    """left_click_drag: moveTo uses start_coordinate, dragTo uses coordinate."""
    step = _make_step(
        1,
        "pyautogui.moveTo(1500, 300, duration=0.5)\npyautogui.dragTo(1800, 600, duration=0.5)\n",
        {
            "action": "left_click_drag",
            "coordinate": [1200, 400],
            "start_coordinate": [1000, 200],
        },
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    code = result["steps"][0]["action_code"]
    assert "pyautogui.moveTo(1000, 200" in code
    assert "pyautogui.dragTo(1200, 400" in code
    # Trailing duration args preserved
    assert "duration=0.5" in code


def test_normalize_scroll_with_coordinates():
    """scroll(amount, x, y): amount preserved, x/y replaced."""
    step = _make_step(
        1,
        "pyautogui.scroll(-3, 1500, 600)\n",
        {"action": "scroll", "coordinate": [1000, 400]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.scroll(-3, 1000, 400)" in result["steps"][0]["action_code"]


def test_normalize_hscroll_with_coordinates():
    """hscroll(amount, x, y): amount preserved, x/y replaced."""
    step = _make_step(
        1,
        "pyautogui.hscroll(5, 1500, 600)\n",
        {"action": "scroll", "coordinate": [1000, 400]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.hscroll(5, 1000, 400)" in result["steps"][0]["action_code"]


def test_normalize_middle_click():
    step = _make_step(
        1,
        "pyautogui.middleClick(900, 600)\n",
        {"action": "middle_click", "coordinate": [600, 400]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert "pyautogui.middleClick(600, 400)" in result["steps"][0]["action_code"]


def test_normalize_keyboard_action_unchanged():
    """Steps without coordinates (keyboard) should pass through unchanged."""
    step = _make_step(
        1,
        "pyautogui.hotkey('ctrl', 'c')\n",
        {"action": "key", "text": "ctrl+c"},  # no coordinate key
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert result["steps"][0]["action_code"] == "pyautogui.hotkey('ctrl', 'c')\n"


def test_normalize_screenshot_action_unchanged():
    """screenshot action_type should pass through unchanged."""
    step = _make_step(
        1,
        "screenshot",
        {},
        action_type="screenshot",
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    assert result["steps"][0]["action_code"] == "screenshot"


def test_normalize_legacy_format_unchanged():
    """Legacy-format trajectories should pass through unchanged."""
    step = {
        "step_num": 1,
        "action_code": "pyautogui.click(500, 600)\n",
        "reasoning": "",
        "error": "",
        "reward": 0,
        "done": False,
        "action_type": "code",
        "screenshot_path": None,
        "action_input": {},
    }
    traj = _make_traj_dict([step], fmt="legacy")
    result = normalize_trajectory(traj)
    assert result["steps"][0]["action_code"] == "pyautogui.click(500, 600)\n"


def test_normalize_does_not_mutate_original():
    """normalize_trajectory must not modify the input dict."""
    step = _make_step(
        1,
        "pyautogui.click(1890, 139)\n",
        {"action": "left_click", "coordinate": [1260, 93]},
    )
    traj = _make_traj_dict([step])
    original_code = traj["steps"][0]["action_code"]
    normalize_trajectory(traj)
    assert traj["steps"][0]["action_code"] == original_code


def test_normalize_moveto_with_duration():
    """moveTo with trailing duration= arg should preserve it."""
    step = _make_step(
        1,
        "pyautogui.moveTo(1500, 300, duration=0.5)\n",
        {"action": "mouse_move", "coordinate": [1000, 200]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    code = result["steps"][0]["action_code"]
    assert "pyautogui.moveTo(1000, 200, duration=0.5)" in code


def test_normalize_click_with_key_modifier():
    """click wrapped in keyDown/keyUp should only rewrite the click."""
    step = _make_step(
        1,
        "pyautogui.keyDown('ctrl')\npyautogui.click(1500, 300)\npyautogui.keyUp('ctrl')\n",
        {"action": "left_click", "coordinate": [1000, 200]},
    )
    traj = _make_traj_dict([step])
    result = normalize_trajectory(traj)
    code = result["steps"][0]["action_code"]
    assert "pyautogui.keyDown('ctrl')" in code
    assert "pyautogui.click(1000, 200)" in code
    assert "pyautogui.keyUp('ctrl')" in code


# ---------------------------------------------------------------------------
# Task 4: load_normalized_trajectory() wrapper test
# ---------------------------------------------------------------------------

from debugger.trajectory import load_normalized_trajectory


def test_load_normalized_trajectory_wrapper(tmp_path):
    """Wrapper should load and normalize in one call."""
    traj_dir = _make_claude_traj(tmp_path, [
        {
            "step_num": 1,
            "action": {
                "action_type": "tool_use",
                "command": "pyautogui.click(1890, 139)\n",
                "raw_response": "[TEXT] clicking",
                "input": {"action": "left_click", "coordinate": [1260, 93]},
                "name": "computer",
            },
            "reward": 0,
            "done": False,
            "info": {},
            "screenshot_file": "",
        }
    ])
    traj = load_normalized_trajectory(traj_dir)
    assert "pyautogui.click(1260, 93)" in traj["steps"][0]["action_code"]


# ---------------------------------------------------------------------------
# Task 6: Optional end-to-end smoke test with external trajectory data
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

# Optional path to an external trajectory with coordinate mismatch.
_SAMPLE_TRAJ = _Path(__file__).resolve().parent.parent / (
    "results/input_trajectory/optional_external_coordinate_mismatch/task"
)


@pytest.mark.skipif(not _SAMPLE_TRAJ.is_dir(), reason="optional external trajectory not on disk")
def test_e2e_real_trajectory_normalized():
    """Smoke test: real trajectory should have model coords after normalization."""
    traj = load_normalized_trajectory(_SAMPLE_TRAJ)
    assert traj["format"] == "claude"
    # Step 1 is known to have coordinate: [1260, 93] but command: pyautogui.click(1890, 139)
    step1 = traj["steps"][0]
    # After normalization, action_code should contain model coords
    if step1["action_input"].get("coordinate"):
        coord = step1["action_input"]["coordinate"]
        # The model coordinates should appear in the action_code
        assert str(coord[0]) in step1["action_code"]
        assert str(coord[1]) in step1["action_code"]
        # The screen-scale coordinates (1890, 139) should NOT appear
        assert "1890" not in step1["action_code"]
