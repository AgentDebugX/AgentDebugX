"""
Unit tests for the ``confusion_set`` self-tag validator on ``Lesson``
(plan §5.8).  The validator is the single defensive point covering both
the post-distill and post-merge paths because Pydantic v2 runs
``mode="after"`` validators on every model construction.
"""

from __future__ import annotations

import json

import pytest

from debugger.memory.lesson_memory import Lesson


def _new_lesson(**overrides) -> Lesson:
    """Tiny factory for tests — only the overridden fields need to be set."""
    base = dict(
        title="t",
        distilled_lesson="d",
        trigger_condition="tc",
        taxonomy_tag="P1",
        failed_action="fa",
        corrected_action="ca",
        distinguishing_feature="df",
        evidence="e",
        confusion_set=[],
        app_id=None,
        episodic_refs=[],
    )
    base.update(overrides)
    return Lesson(**base)


class TestLessonValidatorStripsSelfTag:
    """The marquee test from §10 — covers post-distill *and* post-merge."""

    def test_self_tag_present_is_stripped(self) -> None:
        lesson = _new_lesson(taxonomy_tag="P1", confusion_set=["P1", "P2"])
        assert lesson.confusion_set == ["P2"]

    def test_self_tag_absent_is_untouched(self) -> None:
        lesson = _new_lesson(taxonomy_tag="G1", confusion_set=["P1", "P2"])
        assert lesson.confusion_set == ["P1", "P2"]

    def test_self_tag_duplicates_all_removed(self) -> None:
        lesson = _new_lesson(
            taxonomy_tag="P1",
            confusion_set=["P1", "P2", "P1", "P3", "P1"],
        )
        assert lesson.confusion_set == ["P2", "P3"]

    def test_validator_runs_on_model_validate(self) -> None:
        """JSON round-trip path (used by Chroma metadata reads) is covered."""
        dirty = _new_lesson(
            taxonomy_tag="R10",
            confusion_set=["R10", "R11"],
        )
        # Re-serialise then re-construct as if loading from disk.
        raw = json.loads(json.dumps(dirty.to_json()))
        # Re-inject the dirty value to simulate a stale on-disk record.
        raw["confusion_set"] = ["R10", "R11", "R12"]
        rebuilt = Lesson.model_validate(raw)
        assert rebuilt.confusion_set == ["R11", "R12"]


class TestLessonValidatorEdgeCases:
    """Edge cases documented in §5.8 bullet list."""

    def test_empty_confusion_set(self) -> None:
        lesson = _new_lesson(taxonomy_tag="P1", confusion_set=[])
        assert lesson.confusion_set == []

    def test_empty_taxonomy_tag_short_circuits(self) -> None:
        lesson = _new_lesson(taxonomy_tag="", confusion_set=["P1", "P2"])
        # No strip when the lesson has no self-tag to compare against.
        assert lesson.confusion_set == ["P1", "P2"]

    def test_order_preserved_after_strip(self) -> None:
        lesson = _new_lesson(
            taxonomy_tag="P1",
            confusion_set=["P3", "P1", "P2", "P4"],
        )
        assert lesson.confusion_set == ["P3", "P2", "P4"]

    def test_only_self_tag_becomes_empty(self) -> None:
        lesson = _new_lesson(taxonomy_tag="P1", confusion_set=["P1", "P1"])
        assert lesson.confusion_set == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])