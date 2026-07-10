"""
Unit tests for the three new RCA exploration tools (plan §6) and the
``dispatch_tool`` extras plumbing they depend on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

import pytest

from debugger.dispatch import dispatch_tool
from debugger.memory.lesson_memory import Lesson
from debugger.tools.lesson_explorer import (
    follow_episodic_ref,
    lookup_lessons_by_taxonomy,
    search_lessons_by_app,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeLessonMemory:
    """Minimal duck-typed stand-in: iterates ``Lesson``s in insertion order."""

    def __init__(self, lessons: Iterable[Lesson]) -> None:
        self._lessons: List[Lesson] = list(lessons)

    def __iter__(self):
        return iter(self._lessons)


class _FakeEpisodicMemory:
    """Stand-in: ``read(id)`` returns the stored dict or ``None``."""

    def __init__(self, records: dict[str, dict]) -> None:
        self._records = records

    def read(self, record_id: str) -> dict | None:
        return self._records.get(record_id)


def _make_lesson(
    *,
    taxonomy_tag: str,
    app_id: str | None = None,
    title: str = "lesson",
    episodic_refs: list[str] | None = None,
) -> Lesson:
    return Lesson(
        title=title,
        distilled_lesson="rule body",
        trigger_condition="when X",
        taxonomy_tag=taxonomy_tag,
        failed_action="did the wrong thing",
        corrected_action="should have done the right thing",
        distinguishing_feature="differentiating note",
        evidence="screenshot shows Y",
        confusion_set=[],
        app_id=app_id,
        episodic_refs=episodic_refs or [],
    )


# ---------------------------------------------------------------------------
# lookup_lessons_by_taxonomy
# ---------------------------------------------------------------------------


class TestLookupLessonsByTaxonomy:
    def test_returns_matching_lessons_up_to_top_k(self) -> None:
        lessons = [
            _make_lesson(taxonomy_tag="G1", title="g1-a"),
            _make_lesson(taxonomy_tag="G1", title="g1-b"),
            _make_lesson(taxonomy_tag="G1", title="g1-c"),
            _make_lesson(taxonomy_tag="P1", title="p1-other"),
        ]
        memory = _FakeLessonMemory(lessons)
        text = lookup_lessons_by_taxonomy(
            lesson_memory=memory,
            taxonomy_tag="G1",
            top_k=2,
        )
        assert "g1-" in text
        # Coverage hint shows we returned 2 out of 3 G1 lessons.
        assert "2 of 3 G1 lessons" in text
        # Other taxonomy code must not leak in.
        assert "p1-other" not in text

    def test_exclude_ids_skips_representative(self) -> None:
        a = _make_lesson(taxonomy_tag="G1", title="g1-rep")
        b = _make_lesson(taxonomy_tag="G1", title="g1-other")
        memory = _FakeLessonMemory([a, b])
        text = lookup_lessons_by_taxonomy(
            lesson_memory=memory,
            taxonomy_tag="G1",
            top_k=5,
            exclude_lesson_ids={str(a.id)},
        )
        assert "g1-other" in text
        assert "g1-rep" not in text

    def test_empty_bucket_emits_no_lessons_returned(self) -> None:
        memory = _FakeLessonMemory([])
        text = lookup_lessons_by_taxonomy(
            lesson_memory=memory,
            taxonomy_tag="R10",
        )
        assert "No lessons returned" in text
        assert "0 of 0 R10 lessons" in text


# ---------------------------------------------------------------------------
# search_lessons_by_app
# ---------------------------------------------------------------------------


class TestSearchLessonsByApp:
    def test_filters_by_app_only(self) -> None:
        lessons = [
            _make_lesson(taxonomy_tag="G1", app_id="chrome", title="ch-g1"),
            _make_lesson(taxonomy_tag="R4", app_id="chrome", title="ch-r4"),
            _make_lesson(taxonomy_tag="G1", app_id="gimp",   title="gm-g1"),
        ]
        memory = _FakeLessonMemory(lessons)
        text = search_lessons_by_app(
            lesson_memory=memory,
            app_id="chrome",
            top_k=5,
        )
        assert "ch-g1" in text
        assert "ch-r4" in text
        assert "gm-g1" not in text

    def test_filters_by_app_and_taxonomy(self) -> None:
        lessons = [
            _make_lesson(taxonomy_tag="G1", app_id="chrome", title="ch-g1"),
            _make_lesson(taxonomy_tag="R4", app_id="chrome", title="ch-r4"),
        ]
        memory = _FakeLessonMemory(lessons)
        text = search_lessons_by_app(
            lesson_memory=memory,
            app_id="chrome",
            taxonomy_tag="R4",
        )
        assert "ch-r4" in text
        assert "ch-g1" not in text


# ---------------------------------------------------------------------------
# follow_episodic_ref
# ---------------------------------------------------------------------------


class TestFollowEpisodicRef:
    def test_existing_ref_returns_summary(self) -> None:
        memory = _FakeEpisodicMemory({
            "ep-1": {
                "task_id": "task-7",
                "app_id":  "chrome",
                "taxonomy_tag": "P1",
                "error_step": 4,
                "agent_intention": "wanted to save the form",
                "error_context": {
                    "error_step": 4,
                    "steps": [
                        {"step_num": 3, "action_code": "click Save"},
                        {"step_num": 4, "action_code": "click File menu"},
                    ],
                },
            },
        })
        text = follow_episodic_ref(
            episodic_memory=memory, episodic_ref="ep-1",
        )
        assert "task-7" in text
        assert "chrome" in text
        assert "P1" in text
        assert "step 3" in text
        assert "step 4" in text

    def test_missing_ref_returns_explicit_message(self) -> None:
        memory = _FakeEpisodicMemory({})
        text = follow_episodic_ref(
            episodic_memory=memory, episodic_ref="nope",
        )
        assert "No episode record found" in text


# ---------------------------------------------------------------------------
# dispatch_tool — routing for the three new RCA tools
# ---------------------------------------------------------------------------


class TestDispatchRoutingForNewTools:
    def _osworld_root(self) -> Path:
        return Path(".")

    def _traj_data(self) -> dict:
        return {
            "task_id": "t",
            "instruction": "i",
            "result_score": None,
            "traj_dir": "",
            "format": "v2",
            "steps": [],
            "system_errors": [],
        }

    def test_dispatch_lookup_lessons_by_taxonomy(self) -> None:
        lesson = _make_lesson(taxonomy_tag="G1", title="g1-a")
        extras = {"lesson_memory": _FakeLessonMemory([lesson])}
        content, _ = dispatch_tool(
            "lookup_lessons_by_taxonomy",
            {"taxonomy_tag": "G1", "top_k": 1},
            self._traj_data(),
            self._osworld_root(),
            extras=extras,
        )
        assert content and content[0]["type"] == "text"
        assert "g1-a" in content[0]["text"]

    def test_dispatch_search_lessons_by_app(self) -> None:
        lesson = _make_lesson(taxonomy_tag="G1", app_id="chrome", title="ch-g1")
        extras = {"lesson_memory": _FakeLessonMemory([lesson])}
        content, _ = dispatch_tool(
            "search_lessons_by_app",
            {"app_id": "chrome"},
            self._traj_data(),
            self._osworld_root(),
            extras=extras,
        )
        assert "ch-g1" in content[0]["text"]

    def test_dispatch_follow_episodic_ref(self) -> None:
        ep_mem = _FakeEpisodicMemory({
            "ep-1": {"task_id": "t-77", "app_id": "files", "taxonomy_tag": "P3"},
        })
        extras = {"episodic_memory": ep_mem}
        content, _ = dispatch_tool(
            "follow_episodic_ref",
            {"episodic_ref": "ep-1"},
            self._traj_data(),
            self._osworld_root(),
            extras=extras,
        )
        assert "t-77" in content[0]["text"]
        assert "files" in content[0]["text"]

    def test_dispatch_missing_extras_returns_structured_error(self) -> None:
        content, _ = dispatch_tool(
            "lookup_lessons_by_taxonomy",
            {"taxonomy_tag": "G1"},
            self._traj_data(),
            self._osworld_root(),
            extras=None,
        )
        assert "ERROR" in content[0]["text"]
        assert "lesson_memory" in content[0]["text"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
