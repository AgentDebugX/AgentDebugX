"""
Unit tests for ``LessonInjector`` + ``CompositeSelector`` (plan §§3–4, §11.1).

Uses a thin in-memory stand-in for ``LessonMemory`` (just an iterable
container) so the tests stay self-contained — no Chroma, no embedding API.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import pytest

from debugger.memory.lesson_injector import (
    CompositeSelector,
    HtmlTaxonomySheetRenderer,
    LessonInjector,
)
from debugger.memory.lesson_memory import Lesson
from debugger.taxonomy import (
    ALL_SUBTYPES,
    TAXONOMY_CATEGORIES,
    TAXONOMY_SUBTYPES,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeLessonMemory:
    """Minimal duck-typed stand-in: ``__iter__`` returns the stored lessons."""

    def __init__(self, lessons: Iterable[Lesson]) -> None:
        self._lessons = list(lessons)

    def __iter__(self):
        return iter(self._lessons)


def _make_lesson(
    *,
    taxonomy_tag: str,
    app_id: str | None = None,
    title: str = "lesson title",
    days_old: float = 0.0,
    episodic_refs: List[str] | None = None,
    confusion_set: List[str] | None = None,
) -> Lesson:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return Lesson(
        title=title,
        distilled_lesson="the lesson body",
        trigger_condition="when this happens",
        taxonomy_tag=taxonomy_tag,
        failed_action="did the wrong thing",
        corrected_action="should have done the right thing",
        distinguishing_feature="differentiating note",
        evidence="screenshot shows X",
        confusion_set=confusion_set or [],
        app_id=app_id,
        episodic_refs=episodic_refs or [],
        created_at=stamp,
    )


# ---------------------------------------------------------------------------
# CompositeSelector — scoring properties
# ---------------------------------------------------------------------------


class TestCompositeSelector:
    def test_recency_breaks_tie_with_equal_coverage(self) -> None:
        old = _make_lesson(taxonomy_tag="P1", days_old=60.0, episodic_refs=["ep1"])
        new = _make_lesson(taxonomy_tag="P1", days_old=0.5, episodic_refs=["ep2"])
        chosen = CompositeSelector().pick([old, new])
        assert chosen is new

    def test_higher_coverage_wins_over_age(self) -> None:
        # Coverage doubles the bucket size while the other side is fresher.
        # Default weights (alpha=beta=0.4) make coverage strongly competitive
        # against recency for any non-trivial coverage delta.
        old_well_covered = _make_lesson(
            taxonomy_tag="P1",
            days_old=20.0,
            episodic_refs=[f"ep{i}" for i in range(8)],
        )
        new_singleton = _make_lesson(
            taxonomy_tag="P1",
            days_old=0.5,
            episodic_refs=["ep_solo"],
        )
        chosen = CompositeSelector().pick([old_well_covered, new_singleton])
        assert chosen is old_well_covered

    def test_pick_empty_returns_none(self) -> None:
        assert CompositeSelector().pick([]) is None

    def test_rank_full_order(self) -> None:
        a = _make_lesson(taxonomy_tag="P1", days_old=0.0, episodic_refs=["x"])
        b = _make_lesson(taxonomy_tag="P1", days_old=10.0, episodic_refs=["y"])
        c = _make_lesson(taxonomy_tag="P1", days_old=30.0, episodic_refs=["z"])
        order = CompositeSelector().rank([c, a, b])
        assert order == [a, b, c]


# ---------------------------------------------------------------------------
# LessonInjector.build — table shape
# ---------------------------------------------------------------------------


class TestLessonInjectorTableShape:
    _CATEGORY_PREFIX_MAP = {
        "Perception":               "P",
        "Grounding & Interaction":  "G",
        "Task Reasoning & Control": "R",
        "External / System":        "S",
        "Infeasible Task":          "IF",
    }

    def test_all_31_codes_and_5_categories_present(self) -> None:
        injector = LessonInjector(lesson_memory=FakeLessonMemory([]))
        sheet = injector.build(app_id=None)
        # Every taxonomy code must appear at least once.
        for tag in ALL_SUBTYPES:
            assert tag in sheet, f"missing taxonomy code {tag} in sheet"
        # Every category prefix must appear in the merged-cell header.
        # Category names containing ``&`` are HTML-escaped (``&amp;``),
        # so we assert by the short prefix instead.
        for category, prefix in self._CATEGORY_PREFIX_MAP.items():
            assert f"<b>{prefix} (" in sheet, (
                f"missing category prefix '{prefix}' in sheet"
            )

    def test_rowspan_matches_category_size(self) -> None:
        injector = LessonInjector(lesson_memory=FakeLessonMemory([]))
        sheet = injector.build(app_id=None)
        # Each category's rowspan must equal the number of subtypes.
        # We anchor on the category prefix (``<b>P (`` etc.) instead of the
        # full category name because the latter is HTML-escaped.
        for category, subtypes in TAXONOMY_SUBTYPES.items():
            expected_span = len(subtypes)
            prefix = self._CATEGORY_PREFIX_MAP[category]
            pattern = re.compile(
                rf'rowspan="{expected_span}"[^>]*>\s*<b>{re.escape(prefix)}\s*\(',
                re.MULTILINE,
            )
            assert pattern.search(sheet), (
                f"expected rowspan={expected_span} for category prefix {prefix!r}"
            )

    def test_empty_memory_yields_no_lesson_everywhere(self) -> None:
        injector = LessonInjector(lesson_memory=FakeLessonMemory([]))
        sheet = injector.build(app_id=None)
        # 31 codes × one cell each → 31 ``<NO-LESSON>`` placeholders.
        assert sheet.count("&lt;NO-LESSON&gt;") == 31


# ---------------------------------------------------------------------------
# LessonInjector.build — citation key prefix
# ---------------------------------------------------------------------------


class TestLessonInjectorCitationKeys:
    def test_citation_key_prefix_emitted_for_filled_cells(self) -> None:
        lesson = _make_lesson(taxonomy_tag="P1")
        injector = LessonInjector(lesson_memory=FakeLessonMemory([lesson]))
        sheet = injector.build(app_id=None)
        # Short-uuid prefix must show up next to the chosen lesson.
        short_id = str(lesson.id).split("-")[0][:8]
        assert f"[L:{short_id}]" in sheet


# ---------------------------------------------------------------------------
# LessonInjector — app-scoped fallback (Mode B vs Mode A)
# ---------------------------------------------------------------------------


class TestLessonInjectorAppFallback:
    def test_mode_b_prefers_current_app(self) -> None:
        chrome_lesson = _make_lesson(
            taxonomy_tag="P1", app_id="chrome", title="chrome-flavored",
            days_old=10.0,
        )
        gimp_lesson = _make_lesson(
            taxonomy_tag="P1", app_id="gimp", title="gimp-flavored",
            days_old=0.5,  # fresher → would win in Mode A
        )
        injector = LessonInjector(
            lesson_memory=FakeLessonMemory([chrome_lesson, gimp_lesson]),
        )
        row = injector.build_row("P1", app_id="chrome")
        assert "chrome-flavored" in row
        assert "gimp-flavored" not in row

    def test_mode_b_falls_back_to_global_when_app_empty(self) -> None:
        gimp_lesson = _make_lesson(
            taxonomy_tag="P1", app_id="gimp", title="gimp-flavored",
        )
        injector = LessonInjector(
            lesson_memory=FakeLessonMemory([gimp_lesson]),
        )
        row = injector.build_row("P1", app_id="chrome")
        # No chrome lesson exists; the global fallback finds the gimp one.
        assert "gimp-flavored" in row

    def test_mode_a_pools_all_apps(self) -> None:
        chrome_lesson = _make_lesson(
            taxonomy_tag="P1", app_id="chrome", title="chrome-flavored",
            days_old=10.0,
        )
        gimp_lesson = _make_lesson(
            taxonomy_tag="P1", app_id="gimp", title="gimp-flavored",
            days_old=0.5,
        )
        injector = LessonInjector(
            lesson_memory=FakeLessonMemory([chrome_lesson, gimp_lesson]),
        )
        row = injector.build_row("P1", app_id=None)
        # Fresher gimp lesson wins under Mode A.
        assert "gimp-flavored" in row

    def test_unfilled_row_emits_no_lesson_token(self) -> None:
        injector = LessonInjector(lesson_memory=FakeLessonMemory([]))
        row = injector.build_row("R4", app_id="chrome")
        assert "&lt;NO-LESSON&gt;" in row


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
