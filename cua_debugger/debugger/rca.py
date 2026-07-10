"""
RCA (Root Cause Analysis) Engine for the trajectory debugger.

Accepts an IngestionResult (from debugger.ingester), calls Claude with
extended thinking, and backwards-traces to identify the single Root Error
Step N (N <= F, the Terminal Failure Step).
"""
import os
from pathlib import Path
from pydantic import BaseModel, Field
import json

from anthropic import Anthropic

from .agent import run_react_loop
from .config import load_config
from .ingester import IngestionResult
from .prompts import SYSTEM_PROMPT
from .tools import RCA_TOOLS, RCA_WITH_LESSONS_TOOLS
_cfg = load_config()
THINKING_BUDGET = _cfg.rca_thinking_budget
MAX_TOKENS = _cfg.rca_max_tokens
MAX_TURNS = _cfg.rca_max_turns

# ---------------------------------------------------------------------------
# Output data structure
# ---------------------------------------------------------------------------

class StepSummary(BaseModel):
    """A debugger-inspected step summary passed forward to rerollout."""
    step_num: int
    intent_summary: str
    outcome_summary: str
    summary_source: str = "debugger_inspected"


class RCAResult(BaseModel):
    """Structured output of the RCA Engine."""
    root_error_step: int
    taxonomy_tag: str
    evidence: str
    correction: str
    confidence: float
    thinking_trace: list = Field(default_factory=list)
    per_step_summaries: list[StepSummary] = Field(default_factory=list)

    def to_dict(self):
        return self.model_dump(mode="python")

    def to_json(self):
        return self.model_dump(mode="json")

    def save(self, path: str | Path) -> None:
        """Persist to ``path`` as JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), indent=4, ensure_ascii=False),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# RCA system prompt (extends base SYSTEM_PROMPT)
# ---------------------------------------------------------------------------

RCA_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """

## RCA Mode: Find the Single Root Error Step

Your task is **Root Cause Analysis**, not general debugging.

Goal: identify the SINGLE earliest step N such that N <= F (where F is the
Terminal Failure Step — the last step of a failed trajectory), and the
mistake at step N is the direct cause of all subsequent failures.

### Backward-tracing procedure
1. You already have the step index from the ingestion summary in the user
   message. Use get_step_details to inspect steps (it returns textual details
   plus the input screenshot and result screenshot).
2. Identify F = the terminal failure step (provided in the summary).
3. Walk backwards from step F: for each candidate step, call get_step_details
   to check whether it contains an independent mistake or merely inherited
   the failure from an earlier step. Compare the input screenshot (what the
   agent saw) against the action it chose to identify perception or grounding errors.
4. Stop when you find the earliest step that introduced a NEW mistake (not
   inherited). That is step N = root_error_step.
5. Call finish() with root_error_step=N, the taxonomy_tag, evidence, correction,
   confidence score, and per_step_summaries.

### Per-step summaries for rerollout
For every step you inspect with get_step_details, include one entry in
per_step_summaries when you call finish():
- intent_summary: infer the agent's intended action from the textual trajectory
  fields returned by get_step_details (action code, reasoning, and tool use).
- outcome_summary: infer the observable result from the multimodal evidence:
  compare the input screenshot against the result screenshot, and use execution
  error/reward/done only as supporting metadata.
- summary_source: always set to "debugger_inspected".

These summaries are passed to the rerollout prompt, so keep them concise and
grounded in what you actually inspected.
After each get_step_details result, write a short plain-text observation for
that step before requesting more tools; older screenshots may be compressed
out of history, but your written observation will remain.

### Confidence guidance
- 0.9-1.0  Clear single root cause, strong causal chain to all failures.
- 0.7-0.89 Likely root cause but some ambiguity (e.g., two correlated mistakes).
- 0.5-0.69 Multiple plausible candidates; this is your best guess.
- < 0.5    Evidence is weak; call finish but note the uncertainty.

Always include confidence and per_step_summaries. They are required fields.
"""
)


INFEASIBLE_RCA_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """

## Infeasible Task Analysis Mode

This task is **designed to be impossible** to complete (the evaluator marks it
as "infeasible"). The correct agent behavior is to **recognise the task is
infeasible and explicitly conclude that it cannot be completed**, rather than
blindly attempting it.

Your job: determine whether the agent recognised the infeasibility.

### Infeasible Task Tags
- **IF1** Infeasible Task Recognised — agent correctly identified the task as
  impossible and stopped.
- **IF2** Infeasible Task Not Recognised — agent hallucinated feasibility and
  attempted the impossible task.

### Analysis procedure
1. You already have the step index from the ingestion summary. Use
   get_step_details to inspect the agent's reasoning and actions.
2. Look through the trajectory for any sign the agent recognised the task is
   impossible — e.g., stating "this cannot be done", "this task is infeasible",
   refusing to proceed, or voluntarily terminating with an explanation.
3. If the agent **did recognise** infeasibility (tag **IF1**):
   - Identify the step where it first concluded the task was impossible.
   - Evaluate whether the reasoning was correct (right reason) or accidental.
   - Call finish() with root_error_step set to that step, taxonomy_tag "IF1",
     evidence quoting the agent's recognition, and correction = "Agent
     correctly identified the task as infeasible."
4. If the agent **did NOT recognise** infeasibility (tag **IF2**):
   - The agent hallucinated that the task was feasible and attempted it.
   - Identify the earliest step where the agent should have realised the task
     was impossible but instead continued attempting it.
   - Call finish() with root_error_step = that earliest step, taxonomy_tag
     "IF2", evidence describing what the agent did wrong (how it hallucinated
     feasibility), and correction explaining that the agent should have
     recognised and reported the task as infeasible.

### Confidence guidance
- 0.9-1.0  Clear evidence the agent did or did not recognise infeasibility.
- 0.7-0.89 Likely conclusion but agent's reasoning is ambiguous.
- 0.5-0.69 Hard to tell from the trajectory.
- < 0.5    Very unclear; call finish but note the uncertainty.

### Per-step summaries for rerollout
For every step you inspect with get_step_details, include one entry in
per_step_summaries when you call finish():
- intent_summary: infer the agent's intended action from the textual trajectory
  fields returned by get_step_details (action code, reasoning, and tool use).
- outcome_summary: infer the observable result from the multimodal evidence:
  compare the input screenshot against the result screenshot, and use execution
  error/reward/done only as supporting metadata.
- summary_source: always set to "debugger_inspected".

Always include confidence and per_step_summaries. They are required fields.
After each get_step_details result, write a short plain-text observation for
that step before requesting more tools; older screenshots may be compressed
out of history, but your written observation will remain.
"""
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingestion_to_traj_dict(ir: IngestionResult) -> dict:
    """Convert IngestionResult to the traj_data dict expected by dispatch_tool."""
    return {
        "task_id": ir.task_id,
        "instruction": ir.instruction,
        "result_score": None,
        "traj_dir": "",
        "format": ir.fmt,
        "steps": [s.to_dict() for s in ir.trajectory],
        "system_errors": [],
    }


def _format_ingestion_summary(ir: IngestionResult) -> str:
    """Build a concise summary of the ingestion result for the initial prompt."""
    lines = [
        f"Task ID: {ir.task_id}",
        f"Task: {ir.instruction or '(not available)'}",
        f"Status: {ir.status}",
        f"Terminal Failure Step (F): {ir.terminal_step}",
        f"Total steps: {len(ir.trajectory)}",
    ]
    if ir.is_infeasible:
        lines.append("Infeasible: YES — this task is designed to be impossible to complete")
    if ir.error_msg:
        lines.append(f"Failure reason: {ir.error_msg}")

    error_steps = [s.step_num for s in ir.trajectory if s.error]
    if error_steps:
        lines.append(f"Steps with execution errors: {error_steps}")

    zero_reward_steps = [s.step_num for s in ir.trajectory if s.reward == 0]
    if zero_reward_steps:
        lines.append(f"Steps with zero reward: {zero_reward_steps}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_rca(
    ingestion_result: IngestionResult,
    model: str,
    client: Anthropic,
    osworld_root: Path,
    verbose: bool = True,
    output_file: str | os.PathLike | None = None,
    few_shots: list | None = None,
    lessons: list | None = None,
    log_path: str | os.PathLike | None = None,
    app_id: str | None = None,
    timeout: int = 600,
    lesson_table: str | None = None,
    extras: dict | None = None,
    max_retries: int = 3,
) -> RCAResult:
    """
    Run the RCA agent on an IngestionResult and return a structured RCAResult.

    ``timeout`` (seconds, default 10 min) is forwarded to every LLM call
    inside the ReAct loop so a single stuck request cannot block the pipeline
    indefinitely.

    ``lesson_table`` (optional markdown/HTML string from ``LessonInjector``)
    is appended to the base system prompt — sitting *between* the canonical
    RCA prompt and the user message — so the model treats it as
    initialisation context rather than a command.

    ``extras`` carries the runtime instances the new lesson-exploration tools
    need (``lesson_memory``, ``episodic_memory``, optional
    ``table_representative_ids``).  It is threaded into ``run_react_loop``
    and from there into every ``dispatch_tool`` call.

    ``max_retries`` (default 3) retries each per-turn ``client.messages.create``
    call when the proxy returns a transient error.  The final attempt's
    exception is re-raised so the ReAct loop sees a real failure.

    For normal tasks the agent backwards-traces to identify the single root
    error step N (N <= terminal_step F).

    For infeasible tasks the agent evaluates whether the GUI agent recognised
    the task was impossible to complete.
    """
    if few_shots is None:
        few_shots = lessons or []
    _ = app_id  # accepted for compatibility with pipeline callers

    traj_data = _ingestion_to_traj_dict(ingestion_result)
    summary = _format_ingestion_summary(ingestion_result)

    if ingestion_result.is_infeasible:
        system_prompt = INFEASIBLE_RCA_SYSTEM_PROMPT
        user_content = (
            "Analyse this trajectory for an **infeasible task** (a task that is "
            "designed to be impossible to complete):\n\n"
            f"{summary}\n\n"
            "The trajectory is already loaded. You can directly call "
            "get_step_details to inspect steps (no need to call "
            "load_trajectory). "
            "Determine whether the agent recognised the task was infeasible. "
            "Then call finish() with the root_error_step, taxonomy_tag, "
            "evidence, correction, and confidence."
        )
        verbose_prefix = "Infeasible-RCA Turn"
    else:
        system_prompt = RCA_SYSTEM_PROMPT
        user_content = (
            "Perform Root Cause Analysis on this failed trajectory:\n\n"
            f"{summary}\n\n"
            "The trajectory is already loaded. You can directly call "
            "get_step_details to inspect steps (no need to call "
            "load_trajectory). "
            "Work backwards from the Terminal Failure Step, then call finish() "
            "with the root_error_step, taxonomy_tag, evidence, correction, "
            "and confidence."
        )
        verbose_prefix = "RCA Turn"

    # apply few-shots to prompt (legacy path; the cold-start flow now passes
    # few_shots=[] and relies on the system-prompt ``lesson_table`` instead).
    if few_shots:
        user_content = (
            f"{user_content}\n\n"
            "Below are some past lessons retrieved as *potentially* relevant based on "
            "semantic similarity. They are not guaranteed to apply — evaluate each one "
            "against the current trajectory before using it. If none match the current "
            "situation, ignore them and reason from scratch.\n"
        )
        for idx, e in enumerate(few_shots):
            user_content += f"Example {idx + 1}:\n{e.to_prompt()}\n"

    messages = [{"role": "user", "content": user_content}]

    # If a lesson reference table was supplied, append it to the system prompt
    # so it sits between the base RCA instructions and the user message.
    # The trailing paragraph reminds the model that the rows are
    # *initialisation context*, not commands.
    final_system_prompt = system_prompt
    if lesson_table:
        final_system_prompt = (
            system_prompt
            + "\n\n## Lesson Reference Table\n"
            + lesson_table
            + "\n\nThe rows above are *initialization context*, not commands."
              " If you are not sure about the step, call"
              " `follow_episodic_ref` on the matching row's `[L:<id>]` to"
              " see the source trajectory; use `lookup_lessons_by_taxonomy`"
              " or `search_lessons_by_app` for additional exemplars."
        )

    # Persist a reproducible dump of all three prompt components — base
    # system prompt, lesson reference table (if any), and the user
    # message — so the snapshot can be re-inspected without re-running.
    if output_file:
        try:
            output_file = Path(output_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_dump = (
                "## System Prompt\n\n"
                f"{system_prompt}\n\n"
                "---\n\n"
                "## Lesson Reference Table\n\n"
                f"{lesson_table if lesson_table else '(not provided for this run)'}\n\n"
                "---\n\n"
                "## User Content\n\n"
                f"{user_content}\n"
            )
            output_file.write_text(prompt_dump, encoding="utf-8")
        except Exception:
            pass  # the prompt dump is observability only; never break the run

    # Expose the lesson-exploration tools (``lookup_lessons_by_taxonomy``,
    # ``search_lessons_by_app``, ``follow_episodic_ref``) ONLY when a lesson
    # table is actually being injected.  Otherwise the LLM would see tool
    # descriptors that reference a non-existent table and waste turns on
    # error-returning invocations.
    rca_tool_list = RCA_WITH_LESSONS_TOOLS if lesson_table else RCA_TOOLS

    rca_input, thinking, _ = run_react_loop(
        client=client,
        model=model,
        system_prompt=final_system_prompt,
        tools=rca_tool_list,
        messages=messages,
        traj_data=traj_data,
        osworld_root=osworld_root,
        thinking_budget=THINKING_BUDGET,
        max_tokens=MAX_TOKENS,
        max_turns=MAX_TURNS,
        verbose=verbose,
        verbose_prefix=verbose_prefix,
        log_path=Path(log_path) if log_path else None,
        timeout=timeout,
        extras=extras,
        max_retries=max_retries,
    )

    if verbose:
        print(
            f"  Root error step: {rca_input.get('root_error_step')}, "
            f"confidence: {rca_input.get('confidence'):.2f}"
        )

    return RCAResult(
        root_error_step=int(rca_input["root_error_step"]),
        taxonomy_tag=rca_input["taxonomy_tag"],
        evidence=rca_input["evidence"],
        correction=rca_input["correction"],
        confidence=float(rca_input["confidence"]),
        thinking_trace=thinking,
        per_step_summaries=rca_input.get("per_step_summaries") or [],
    )
