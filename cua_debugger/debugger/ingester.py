"""
Trajectory ingestion with validation layer.

Wraps load_trajectory() from trajectory.py and produces a structured
IngestionResult without calling any LLM.

Usage:
    from debugger.ingester import IngestionResult

    result = ingest(Path("airbyte/some-task-dir"))
    print(result.status)          # "success" or "failure"
    print(result.terminal_step)   # step_num of the last step (-1 if empty)
    print(result.error_msg)       # failure reason, if any
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from pydantic import BaseModel

from .trajectory import load_normalized_trajectory


class Step(BaseModel):
    """Normalised representation of a single trajectory step."""
    step_num:           int
    action_code:        str
    reasoning:          str
    llm_tool_use:       str = ""
    error:              str
    reward:             float
    done:               bool
    action_type:        str
    screenshot_path:    None | Path

    def __repr__(self) -> str:
        return f"Step(step_num={self.step_num})"

    def to_dict(self):
        return self.model_dump(mode="python")

    def to_json(self):
        return self.model_dump(mode="json")


class IngestionResult(BaseModel):
    """
    Result of ingesting one trajectory directory.

    Attributes:
        status:        "success" if the agent completed the task, "failure" otherwise.
        trajectory:    Ordered list of Step objects.
        terminal_step: step_num of the final (terminal) step; -1 when the
                       trajectory is empty.  On failure this is the Terminal
                       Failure Step F.
        error_msg:     Human-readable failure description, empty on success.
        task_id:       Task identifier derived from the directory / config file.
        instruction:   Natural-language task instruction (may be empty for some
                       legacy trajectories).
        fmt:           "claude" or "legacy" — detected on-disk format.
    """

    status:         Literal["success", "failure"]
    trajectory:     list[Step]
    terminal_step:  int
    error_msg:      str
    task_id:        str = ""
    instruction:    str = ""
    fmt:            str = ""
    is_infeasible:  bool = False

    def to_dict(self):
        return self.model_dump(mode="python")

    def to_json(self):
        return self.model_dump(mode="json")

    @classmethod
    def from_directory(cls, traj_dir: Path) -> "IngestionResult":
        """
        Load and validate a trajectory directory.

        Success criteria:
          - The last step has ``done=True`` **and** ``reward > 0``.

        Failure criteria (any of the following):
          - System-level errors were recorded (``{"Error": ...}`` lines in JSONL).
          - The trajectory is empty.
          - The last step has ``reward == 0`` (regardless of ``done``).
          - The last step has a non-empty ``info.error``.

        Args:
            traj_dir: Path to the trajectory directory.

        Returns:
            IngestionResult with all fields populated.

        Raises:
            FileNotFoundError: If the directory or JSONL file does not exist.
        """
        raw = load_normalized_trajectory(Path(traj_dir))

        steps = [Step(**s) for s in raw["steps"]]

        system_errors: list[str] = raw.get("system_errors", [])
        is_infeasible = raw.get("evaluator_func", "") == "infeasible"

        # --- determine status ---
        if not steps:
            # Nothing was executed at all — treat as failure
            error_msg = system_errors[0] if system_errors else "empty trajectory"
            return cls(
                status="failure",
                trajectory=steps,
                terminal_step=-1,
                error_msg=error_msg,
                task_id=raw.get("task_id", ""),
                instruction=raw.get("instruction", ""),
                fmt=raw.get("format", ""),
                is_infeasible=is_infeasible,
            )

        last_step = steps[-1]
        terminal_step = last_step.step_num

        # Build error_msg from available signals (priority: step error > system error)
        step_error = last_step.error.strip() if last_step.error else ""
        sys_error = system_errors[0].strip() if system_errors else ""
        combined_error = step_error or sys_error

        # Success requires done=True AND positive reward
        if last_step.done and last_step.reward > 0:
            status: Literal["success", "failure"] = "success"
            error_msg = ""
        else:
            status = "failure"
            # Construct a descriptive message when nothing more specific is available
            if not combined_error:
                if not last_step.done:
                    combined_error = f"trajectory terminated without done=True at step {terminal_step}"
                else:
                    combined_error = f"task scored reward={last_step.reward} at step {terminal_step}"
            error_msg = combined_error

        return cls(
            status=status,
            trajectory=steps,
            terminal_step=terminal_step,
            error_msg=error_msg,
            task_id=raw.get("task_id", ""),
            instruction=raw.get("instruction", ""),
            fmt=raw.get("format", ""),
            is_infeasible=is_infeasible,
        )
