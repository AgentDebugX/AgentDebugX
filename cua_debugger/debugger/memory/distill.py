"""
Step 2A — Lesson distillation.

Three distillation modes:

1. ``distill_lesson`` — original single-trajectory distillation from EC_t + RCA.
2. ``distill_contrastive`` — Level 1: compare a failed trajectory with a
   successful trajectory for the same task (e.g. 15-step fail vs 50-step success).
3. ``distill_from_annotation`` — Level 2: compare a failed trajectory's RCA
   with a human annotation to produce a ground-truth-aligned lesson.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from debugger.prompts import (
    LESSON_DISTILLATION_PROMPT,
    CONTRASTIVE_DISTILLATION_PROMPT,
    ANNOTATION_DISTILLATION_PROMPT,
)
from debugger.memory.lesson_memory import Lesson

def _format_distill_user_message(ec_t: dict, rca: dict, intention: str) -> str:
    lines = [
        "## Agent Intention",
        intention or "(none)",
        "",
        "## Error Context (EC_t)",
        f"Error step: {ec_t.get('error_step')}",
    ]
    for s in ec_t.get("steps", []):
        marker = "  <-- error" if s.get("step_num") == ec_t.get("error_step") else ""
        lines.append(f"--- Step {s.get('step_num')}{marker} ---")
        lines.append(f"action_code: {s.get('action_code', '')}")
        if s.get("reasoning"):
            lines.append(f"reasoning: {s['reasoning']}")
        if s.get("error"):
            lines.append(f"error: {s['error']}")
    lines += [
        "",
        "## RCA Result",
        f"root_error_step: {rca.get('root_error_step')}",
        f"taxonomy_tag:    {rca.get('taxonomy_tag')}",
        f"evidence:        {rca.get('evidence')}",
        f"correction:      {rca.get('correction')}",
        f"confidence:      {rca.get('confidence')}",
        "",
        "Now produce the Lesson JSON.",
    ]
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # drop first line (``` or ```json) and trailing fence
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def distill_lesson(
    ec_t: dict,
    rca: dict,
    intention: str,
    client,
    model: str,
    *,
    app_id: str = "",
    episodic_ref: Optional[str] = None,
    max_tokens: int = 1500,
) -> Lesson:
    """
    Single LLM call → structured Lesson.

    Raises ValueError if the model returns malformed JSON.
    """
    user = _format_distill_user_message(ec_t, rca, intention)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=LESSON_DISTILLATION_PROMPT,
        messages=[{"role": "user", "content": user}],
    )

    text_parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw = _strip_code_fence("".join(text_parts))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"distill_lesson: model did not return valid JSON: {e}\nRaw: {raw[:500]}") from e

    return Lesson(
        title=str(data.get("title", "")),
        distilled_lesson=str(data.get("distilled_lesson", "")),
        trigger_condition=str(data.get("trigger_condition", "")),
        taxonomy_tag=str(data.get("taxonomy_tag", rca.get("taxonomy_tag", ""))),
        failed_action=str(data.get("failed_action", "")),
        corrected_action=str(data.get("corrected_action", "")),
        distinguishing_feature=str(data.get("distinguishing_feature", "")),
        evidence=str(data.get("evidence", "")),
        confusion_set=list(data.get("confusion_set", [])),
        app_id=app_id,
        episodic_refs=[episodic_ref] if episodic_ref else [],
    )


# ---------------------------------------------------------------------------
# Shared helpers for trajectory formatting
# ---------------------------------------------------------------------------

def _format_steps(steps: list[dict], label: str, max_steps: int = 50) -> str:
    """Format a list of step dicts into a readable block."""
    lines = [f"## {label} ({len(steps)} steps)"]
    for s in steps[:max_steps]:
        lines.append(f"--- Step {s.get('step_num', '?')} ---")
        lines.append(f"action_type: {s.get('action_type', '')}")
        lines.append(f"action_code: {s.get('action_code', '')}")
        if s.get("reasoning"):
            # Truncate long reasoning to keep context manageable
            reasoning = s["reasoning"]
            if len(reasoning) > 600:
                reasoning = reasoning[:600] + "..."
            lines.append(f"reasoning: {reasoning}")
        if s.get("error"):
            lines.append(f"error: {s['error']}")
        lines.append(f"reward: {s.get('reward', 0)}  done: {s.get('done', False)}")
        lines.append("")
    if len(steps) > max_steps:
        lines.append(f"... ({len(steps) - max_steps} more steps omitted)")
    return "\n".join(lines)


def _parse_llm_json(response) -> dict:
    """Extract and parse JSON from an LLM response."""
    text_parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw = _strip_code_fence("".join(text_parts))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model did not return valid JSON: {e}\nRaw: {raw}"
        ) from e


# ---------------------------------------------------------------------------
# Level 1 — Contrastive distillation (fail vs success trajectories)
# ---------------------------------------------------------------------------

def _format_contrastive_message(
    instruction: str,
    fail_steps: list[dict],
    success_steps: list[dict],
    rca: dict,
) -> str:
    lines = [
        "## Task Instruction",
        instruction or "(not available)",
        "",
        "## RCA Result (for the failed trajectory)",
        f"root_error_step: {rca.get('root_error_step')}",
        f"taxonomy_tag:    {rca.get('taxonomy_tag')}",
        f"evidence:        {rca.get('evidence')}",
        f"correction:      {rca.get('correction')}",
        f"confidence:      {rca.get('confidence')}",
        "",
        _format_steps(fail_steps, "FAILED Trajectory"),
        "",
        _format_steps(success_steps, "SUCCESSFUL Trajectory"),
        "",
        "Now compare the two trajectories and produce the Lesson JSON.",
    ]
    return "\n".join(lines)


def distill_contrastive(
    instruction: str,
    fail_steps: list[dict],
    success_steps: list[dict],
    rca: dict,
    client,
    model: str,
    *,
    app_id: str = "",
    episodic_ref: Optional[str] = None,
    max_tokens: int = 4000,
) -> Lesson:
    """
    Level 1 contrastive distillation: failed trajectory vs successful trajectory.

    Raises ValueError if the model returns malformed JSON.
    """
    user = _format_contrastive_message(instruction, fail_steps, success_steps, rca)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=CONTRASTIVE_DISTILLATION_PROMPT,
        messages=[{"role": "user", "content": user}],
    )

    data = _parse_llm_json(response)

    return Lesson(
        title=str(data.get("title", "")),
        distilled_lesson=str(data.get("distilled_lesson", "")),
        trigger_condition=str(data.get("trigger_condition", "")),
        taxonomy_tag=str(data.get("taxonomy_tag", rca.get("taxonomy_tag", ""))),
        failed_action=str(data.get("failed_action", "")),
        corrected_action=str(data.get("corrected_action", "")),
        distinguishing_feature=str(data.get("distinguishing_feature", "")),
        evidence=str(data.get("evidence", "")),
        confusion_set=list(data.get("confusion_set", [])),
        app_id=app_id,
        episodic_refs=[episodic_ref] if episodic_ref else [],
    )


# ---------------------------------------------------------------------------
# Level 2 — Annotation-based distillation (fail trajectory + human annotation)
# ---------------------------------------------------------------------------

def _format_annotation_message(
    instruction: str,
    fail_steps: list[dict],
    rca: dict,
    annotation: dict,
) -> str:
    lines = [
        "## Task Instruction",
        instruction or "(not available)",
        "",
        "## LLM's Root Cause Analysis Result",
        f"root_error_step: {rca.get('root_error_step')}",
        f"taxonomy_tag:    {rca.get('taxonomy_tag')}",
        f"evidence:        {rca.get('evidence')}",
        f"correction:      {rca.get('correction')}",
        f"confidence:      {rca.get('confidence')}",
        "",
        "## Human Annotation (ground truth)",
        f"annotator:       {annotation.get('annotator', '')}",
        f"root_error_step: {annotation.get('root_error_step')}",
        f"taxonomy_tag:    {annotation.get('taxonomy_tag')}",
        f"evidence:        {annotation.get('evidence')}",
        f"correction:      {annotation.get('correction')}",
        f"confidence:      {annotation.get('confidence')}",
        "",
        _format_steps(fail_steps, "FAILED Trajectory"),
        "",
        "Now compare the LLM's RCA with the human annotation and produce the Lesson JSON",
        "## Output Format"
        "Return ONLY a single, valid, only ASCII JSON object (no markdown fences, no prose, no trailing text, no explanation, no reasoning) matching exactly this schema:"
        "{"
        '    "title": <str>,'
        '    "distilled_lesson": <str>,'
        '    "trigger_condition": <str>,'
        '    "failed_action": <str>,'
        '    "corrected_action": <str>,'
        '    "distinguishing_feature": <str>,'
        '    "confusion_set": <list[str]>,'
        '    "evidence": <str>'
        "}"
    ]
    return "\n".join(lines)


def distill_from_annotation(
    instruction: str,
    fail_steps: list[dict],
    rca: dict,
    annotation: dict,
    client,
    model: str,
    *,
    app_id: str = "",
    episodic_ref: Optional[str] = None,
    max_tokens: int = 4000,
    timeout: int = 600,
    max_retries: int = 3,
) -> Lesson:
    """
    Level 2 annotation-based distillation: failed trajectory + human annotation.

    ``timeout`` (seconds, default 10 min) bounds the single LLM call used to
    produce the lesson — propagated to ``client.messages.create``.

    ``max_retries`` (default 3) retries the *combined* "LLM call + JSON parse"
    step when the proxy returns a transient error (e.g. 400, 5xx, connection
    drop) or when the model emits truncated / malformed JSON.  The final
    attempt's exception is re-raised so callers see a real failure.

    Raises ValueError if the model returns malformed JSON on every attempt.
    """
    user = _format_annotation_message(instruction, fail_steps, rca, annotation)

    data = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=ANNOTATION_DISTILLATION_PROMPT,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
            )
            data = _parse_llm_json(response)
            break
        except Exception as exc:  # noqa: BLE001 — every failure mode is retryable
            if attempt == max_retries:
                raise

    # Prefer human annotation's taxonomy tag
    tag = str(data.get("taxonomy_tag", annotation.get("taxonomy_tag", rca.get("taxonomy_tag", ""))))

    return Lesson(
        title=str(data.get("title", "")),
        distilled_lesson=str(data.get("distilled_lesson", "")),
        trigger_condition=str(data.get("trigger_condition", "")),
        taxonomy_tag=tag,
        failed_action=str(data.get("failed_action", "")),
        corrected_action=str(data.get("corrected_action", "")),
        distinguishing_feature=str(data.get("distinguishing_feature", "")),
        evidence=str(data.get("evidence", "")),
        confusion_set=list(data.get("confusion_set", [])),
        app_id=app_id,
        episodic_refs=[episodic_ref] if episodic_ref else [],
    )
