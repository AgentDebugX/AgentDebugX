from pathlib import Path

from debugger.ingester import IngestionResult, Step
from debugger.memory.intention import build_error_context


def _step(n: int) -> Step:
    return Step(
        step_num=n,
        action_code=f"click({n})",
        reasoning=f"reason {n}",
        error="",
        reward=0.0,
        done=False,
        action_type="click",
        screenshot_path=Path(f"/tmp/s{n}.png"),
    )


def _ir(num_steps: int) -> IngestionResult:
    return IngestionResult(
        status="failure",
        trajectory=[_step(i) for i in range(num_steps)],
        terminal_step=num_steps - 1,
        error_msg="",
        task_id="t",
        instruction="i",
    )


def test_window_middle():
    ec = build_error_context(_ir(5), error_step=2)
    assert [s["step_num"] for s in ec["steps"]] == [1, 2, 3]
    assert ec["error_step"] == 2
    assert ec["window"] == [1, 3]


def test_window_clipped_at_start():
    ec = build_error_context(_ir(5), error_step=0)
    assert [s["step_num"] for s in ec["steps"]] == [0, 1]


def test_window_clipped_at_end():
    ec = build_error_context(_ir(5), error_step=4)
    assert [s["step_num"] for s in ec["steps"]] == [3, 4]


def test_includes_screenshot_paths():
    ec = build_error_context(_ir(5), error_step=2)
    assert all("screenshot_path" in s for s in ec["steps"])
    assert ec["steps"][1]["screenshot_path"] == str(Path("/tmp/s2.png"))
