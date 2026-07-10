"""
Pure-Python implementations of the three lesson-exploration tools.

These callables sit one layer below the Anthropic-style tool descriptors
in ``debugger/tools/__init__.py``.  The dispatcher in ``debugger/dispatch.py``
binds them to the runtime ``LessonMemory`` / ``EpisodicMemory`` /
``LessonInjector`` instances and translates ``tool_use`` JSON into kwargs.

Each function returns a single string suitable for inclusion in a
``tool_result`` content block.  Every result ends with a one-line
*coverage hint* (e.g. ``"Coverage: 3 of 18 G1 lessons returned"``) so the
RCA model can decide whether to call again with a larger ``top_k``.
"""

from __future__ import annotations

from typing import Any, List, Optional

from debugger.memory.lesson_injector import (
    CompositeSelector,
    LessonSelector,
)
from debugger.memory.lesson_memory import Lesson, LessonMemory


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _select_top_k(
    bucket: List[Lesson],
    *,
    top_k: int,
    selector: LessonSelector,
    exclude_ids: Optional[set[str]] = None,
) -> List[Lesson]:
    """Apply the selector's ranking and trim to ``top_k`` — uniform helper."""
    exclude_ids = exclude_ids or set()
    ordered = selector.rank(bucket)
    filtered: List[Lesson] = []
    for lesson in ordered:
        if str(lesson.id) in exclude_ids:
            continue
        filtered.append(lesson)
        if len(filtered) >= top_k:
            break
    return filtered


def _format_lessons_for_prompt(
    lessons: List[Lesson],
    *,
    total_in_bucket: int,
    bucket_label: str,
) -> str:
    """Render the lesson list into the standard tool_result text block."""
    if not lessons:
        return f"No lessons returned. Coverage: 0 of {total_in_bucket} {bucket_label}."

    blocks: list[str] = []
    for lesson in lessons:
        short_id = str(lesson.id).split("-")[0][:8]
        blocks.append(f"**[L:{short_id}]**\n{lesson.to_prompt()}")
    coverage_hint = (
        f"Coverage: {len(lessons)} of {total_in_bucket} {bucket_label} returned."
    )
    return "\n\n---\n\n".join(blocks) + "\n\n" + coverage_hint


# ---------------------------------------------------------------------------
# Tool 1: lookup_lessons_by_taxonomy
# ---------------------------------------------------------------------------


def lookup_lessons_by_taxonomy(
    *,
    lesson_memory: LessonMemory,
    taxonomy_tag: str,
    top_k: int = 3,
    selector: Optional[LessonSelector] = None,
    exclude_lesson_ids: Optional[set[str]] = None,
) -> str:
    """Return ``top_k`` extra lessons sharing ``taxonomy_tag``.

    ``exclude_lesson_ids`` is the set of representative-IDs already shown
    in the injected reference table; passing it keeps the tool from
    handing back the same lesson the table already contains.
    """
    selector = selector or CompositeSelector()
    bucket: list[Lesson] = [
        lesson for lesson in lesson_memory
        if lesson.taxonomy_tag == taxonomy_tag
    ]
    chosen = _select_top_k(
        bucket,
        top_k=top_k,
        selector=selector,
        exclude_ids=exclude_lesson_ids,
    )
    return _format_lessons_for_prompt(
        chosen,
        total_in_bucket=len(bucket),
        bucket_label=f"{taxonomy_tag} lessons",
    )


# ---------------------------------------------------------------------------
# Tool 2: search_lessons_by_app
# ---------------------------------------------------------------------------


def search_lessons_by_app(
    *,
    lesson_memory: LessonMemory,
    app_id: str,
    taxonomy_tag: Optional[str] = None,
    top_k: int = 3,
    selector: Optional[LessonSelector] = None,
) -> str:
    """Filter lessons by ``app_id`` (and optionally ``taxonomy_tag``)."""
    selector = selector or CompositeSelector()
    bucket: list[Lesson] = []
    for lesson in lesson_memory:
        if lesson.app_id != app_id:
            continue
        if taxonomy_tag is not None and lesson.taxonomy_tag != taxonomy_tag:
            continue
        bucket.append(lesson)
    chosen = _select_top_k(bucket, top_k=top_k, selector=selector)

    if taxonomy_tag:
        bucket_label = f"{taxonomy_tag} lessons under app_id={app_id!r}"
    else:
        bucket_label = f"lessons under app_id={app_id!r}"
    return _format_lessons_for_prompt(
        chosen,
        total_in_bucket=len(bucket),
        bucket_label=bucket_label,
    )


# ---------------------------------------------------------------------------
# Tool 3: follow_episodic_ref
# ---------------------------------------------------------------------------


def follow_episodic_ref(
    *,
    episodic_memory: Any,  # avoid hard import of EpisodicMemory here
    episodic_ref: str,
    error_context_max_steps: int = 3,
) -> str:
    """Resolve an episodic_ref UUID into a compact failure summary.

    ``episodic_memory`` is duck-typed as ``debugger.memory.EpisodicMemory``;
    we use ``read`` and accept ``None`` for missing entries.  The optional
    ``error_context_max_steps`` caps the size of the embedded EC_t window.
    """
    record = episodic_memory.read(episodic_ref)
    if record is None:
        return f"No episode record found for episodic_ref={episodic_ref!r}."

    error_context_summary = _summarize_error_context(
        record.get("error_context"),
        max_steps=error_context_max_steps,
    )
    summary_lines = [
        f"task_id:       {record.get('task_id', '')}",
        f"app_id:        {record.get('app_id', '')}",
        f"taxonomy_tag:  {record.get('taxonomy_tag', '')}",
        f"error_step:    {record.get('error_step', '')}",
        f"intention:     {record.get('agent_intention') or '(none)'}",
        "error_context:",
        error_context_summary,
    ]
    return "\n".join(summary_lines)


def _summarize_error_context(
    error_context: Optional[dict],
    *,
    max_steps: int,
) -> str:
    """One-line-per-step summary of the EC_t window, capped at ``max_steps``."""
    if not error_context or not isinstance(error_context, dict):
        return "  (not available)"
    steps = error_context.get("steps") or []
    if not steps:
        return "  (empty)"
    out_lines: list[str] = []
    for step in steps[:max_steps]:
        marker = "  <- error" if step.get("step_num") == error_context.get("error_step") else ""
        action_code = (step.get("action_code") or "").splitlines()[0][:120]
        out_lines.append(
            f"  step {step.get('step_num')}{marker}: {action_code}"
        )
    if len(steps) > max_steps:
        out_lines.append(f"  … ({len(steps) - max_steps} more steps omitted)")
    return "\n".join(out_lines)


__all__ = [
    "lookup_lessons_by_taxonomy",
    "search_lessons_by_app",
    "follow_episodic_ref",
]