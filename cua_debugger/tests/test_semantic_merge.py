"""
Unit tests for the four router branches in ``SemanticMergeRule`` /
``MergeRouter`` (plan §5).

We avoid the real ``LessonMemory`` (which would need Chroma + embedding API)
by injecting a stub at the only point the router touches it: the
``database.similarity_search_with_relevance_scores`` call.  All four router
actions are exercised below.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, List

import pytest

from debugger.memory.lesson_memory import Lesson
from debugger.memory.semantic_merge import (
    AddAction,
    HyDeLessonLookup,
    MergeAction,
    MergeRouter,
    NoopAction,
    RouterThresholds,
    SemanticMergeRule,
    UpdateAction,
    jaccard_similarity,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeDocument:
    """Minimal stand-in for ``langchain_core.documents.Document``."""

    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata


class _FakeChromaDatabase:
    """Returns a programmed list of (Document, score) tuples."""

    def __init__(self, hits: list[tuple[_FakeDocument, float]]) -> None:
        self._hits = hits

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int,
        filter: dict | None = None,
    ) -> list[tuple[_FakeDocument, float]]:
        return list(self._hits)


class _FakeLessonMemory:
    """The router only touches ``self.database`` from inside ``add``."""

    def __init__(self, hits: list[tuple[_FakeDocument, float]]) -> None:
        self.database = _FakeChromaDatabase(hits)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lesson(
    *,
    taxonomy_tag: str = "G1",
    app_id: str | None = "chrome",
    title: str = "lesson",
    failed_action: str = "click the save button on the toolbar",
    evidence: str = "screenshot shows save button at 100,200 was missed",
    confusion_set: list[str] | None = None,
    episodic_refs: list[str] | None = None,
) -> Lesson:
    return Lesson(
        title=title,
        distilled_lesson="the rule",
        trigger_condition="when this happens",
        taxonomy_tag=taxonomy_tag,
        failed_action=failed_action,
        corrected_action="do X",
        distinguishing_feature="feature",
        evidence=evidence,
        confusion_set=confusion_set or [],
        app_id=app_id,
        episodic_refs=episodic_refs or [],
    )


def _build_hits(neighbors: Iterable[Lesson], scores: Iterable[float]) -> list:
    return [
        (_FakeDocument(metadata=neighbor.to_json()), score)
        for neighbor, score in zip(neighbors, scores)
    ]


# ---------------------------------------------------------------------------
# Router action tests
# ---------------------------------------------------------------------------


class TestMergeRouter:
    def test_no_neighbors_yields_add(self) -> None:
        memory = _FakeLessonMemory(hits=[])
        router = MergeRouter(lesson_memory=memory)
        candidate = _make_lesson()
        action = router.route(candidate)
        assert isinstance(action, AddAction)

    def test_low_similarity_neighbor_yields_add(self) -> None:
        neighbor = _make_lesson(
            failed_action="completely unrelated action that does not share words",
            evidence="entirely different observation",
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.50]))  # below 0.85
        router = MergeRouter(lesson_memory=memory)
        action = router.route(_make_lesson())
        assert isinstance(action, AddAction)

    def test_one_strong_neighbor_with_new_info_yields_update(self) -> None:
        neighbor = _make_lesson(
            failed_action="click the save button on the toolbar",
            evidence="completely different evidence sentence",
        )
        candidate = _make_lesson(
            failed_action="click the save button on the toolbar",
            evidence=(
                "candidate evidence brings several genuinely brand new "
                "tokens xyzzy frobnicate plover quantum"
            ),
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.90]))
        router = MergeRouter(lesson_memory=memory)
        action = router.route(candidate)
        assert isinstance(action, UpdateAction)
        assert action.neighbor.id == neighbor.id

    def test_one_strong_paraphrase_yields_noop(self) -> None:
        # Almost-identical evidence → information_gain below ε → NOOP.
        shared_evidence = (
            "screenshot shows save button at 100,200 was missed"
        )
        neighbor = _make_lesson(evidence=shared_evidence)
        candidate = _make_lesson(evidence=shared_evidence)
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.93]))
        router = MergeRouter(lesson_memory=memory)
        action = router.route(candidate)
        assert isinstance(action, NoopAction)
        assert action.absorbed_by.id == neighbor.id

    def test_multiple_strong_neighbors_yield_merge(self) -> None:
        n1 = _make_lesson(
            failed_action="click the save button on the toolbar",
            evidence="distinct evidence one",
        )
        n2 = _make_lesson(
            failed_action="click the save button on the toolbar",
            evidence="distinct evidence two",
        )
        memory = _FakeLessonMemory(
            hits=_build_hits([n1, n2], [0.92, 0.88]),
        )
        router = MergeRouter(lesson_memory=memory)
        action = router.route(_make_lesson())
        assert isinstance(action, MergeAction)
        assert len(action.neighbors) == 2

    def test_router_excludes_self_id_from_neighbors(self) -> None:
        # The candidate has just been added to the store, so the search may
        # surface itself. The router must filter that out.
        candidate = _make_lesson()
        memory = _FakeLessonMemory(hits=_build_hits([candidate], [0.99]))
        router = MergeRouter(lesson_memory=memory)
        action = router.route(candidate)
        assert isinstance(action, AddAction)

    def test_failed_action_jaccard_gate_blocks_merge(self) -> None:
        # Cosine high, but failed_action overlap below δ_act → ADD.
        neighbor = _make_lesson(
            failed_action="open settings panel and toggle dark mode",
        )
        candidate = _make_lesson(
            failed_action="entirely orthogonal phrase about tab switching",
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.99]))
        router = MergeRouter(lesson_memory=memory)
        action = router.route(candidate)
        assert isinstance(action, AddAction)


# ---------------------------------------------------------------------------
# SemanticMergeRule.merge_func — field-by-field behaviour
# ---------------------------------------------------------------------------


class _FakeLLMResponseBlock:
    def __init__(self, text: str) -> None:
        self.text = text
        self.type = "text"


class _FakeLLMResponse:
    def __init__(self, content_text: str) -> None:
        self.content = [_FakeLLMResponseBlock(text=content_text)]


class _FakeLLMMessages:
    def __init__(self, response_payload: dict) -> None:
        self._payload = response_payload

    def create(self, **kwargs):
        import json
        return _FakeLLMResponse(content_text=json.dumps(self._payload))


class _FakeLLMClient:
    def __init__(self, response_payload: dict) -> None:
        self.messages = _FakeLLMMessages(response_payload)


def _build_rule(memory, llm_payload: dict | None = None) -> SemanticMergeRule:
    return SemanticMergeRule(
        lesson_memory=memory,
        client=_FakeLLMClient(
            response_payload=llm_payload or {
                "title": "fused title",
                "distilled_lesson": "fused lesson",
                "trigger_condition": "fused trigger",
                "distinguishing_feature": "fused feature",
                "evidence": "fused evidence with sources [L:abc]",
            },
        ),
        model="dummy-model",
    )


class TestSemanticMergeFieldRules:
    def test_merge_action_unions_episodic_refs(self) -> None:
        n1 = _make_lesson(
            failed_action="click save",
            evidence="ev1 distinct enough",
            episodic_refs=["ep-a"],
        )
        n2 = _make_lesson(
            failed_action="click save",
            evidence="ev2 distinct enough",
            episodic_refs=["ep-b"],
        )
        candidate = _make_lesson(
            failed_action="click save",
            evidence="brand new candidate evidence",
            episodic_refs=["ep-c"],
        )
        memory = _FakeLessonMemory(
            hits=_build_hits([n1, n2], [0.92, 0.89]),
        )
        rule = _build_rule(memory)

        ids = rule(candidate)
        assert set(ids) == {candidate.id, n1.id, n2.id}

        merged = rule.merge_func([candidate, n1, n2])
        assert len(merged) == 1
        assert set(merged[0].episodic_refs) == {"ep-a", "ep-b", "ep-c"}

    def test_update_action_inherits_union(self) -> None:
        neighbor = _make_lesson(
            failed_action="click save",
            evidence="neighbor evidence",
            episodic_refs=["ep-neighbor"],
        )
        candidate = _make_lesson(
            failed_action="click save",
            evidence=(
                "candidate evidence with brand new tokens xyzzy frobnicate "
                "plover quantum to clear epsilon gate"
            ),
            episodic_refs=["ep-candidate"],
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.91]))
        rule = _build_rule(memory)

        ids = rule(candidate)
        assert set(ids) == {candidate.id, neighbor.id}

        merged = rule.merge_func([candidate, neighbor])
        assert set(merged[0].episodic_refs) == {"ep-neighbor", "ep-candidate"}

    def test_noop_absorbs_into_neighbor(self) -> None:
        shared_evidence = "exact same evidence to trigger paraphrase NOOP path"
        neighbor = _make_lesson(
            evidence=shared_evidence,
            episodic_refs=["ep-old"],
        )
        candidate = _make_lesson(
            evidence=shared_evidence,
            episodic_refs=["ep-new"],
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.95]))
        rule = _build_rule(memory)

        ids = rule(candidate)
        assert set(ids) == {candidate.id, neighbor.id}

        merged = rule.merge_func([candidate, neighbor])
        # The neighbor's refs get unioned with the candidate's.
        assert set(merged[0].episodic_refs) == {"ep-old", "ep-new"}

    def test_add_returns_empty_ids(self) -> None:
        memory = _FakeLessonMemory(hits=[])
        rule = _build_rule(memory)
        ids = rule(_make_lesson())
        assert ids == []

    def test_noop_strips_self_tag_from_confusion_set(self) -> None:
        """NOOP path uses Lesson(**dict) so the validator fires (regression)."""
        shared_evidence = "exact same evidence to trigger NOOP path"
        neighbor = _make_lesson(
            taxonomy_tag="G1",
            evidence=shared_evidence,
            confusion_set=["G1", "G4"],   # bug: self-tag included
        )
        candidate = _make_lesson(
            taxonomy_tag="G1",
            evidence=shared_evidence,
            confusion_set=["G1", "G5"],
        )
        memory = _FakeLessonMemory(hits=_build_hits([neighbor], [0.93]))
        rule = _build_rule(memory)
        rule(candidate)
        absorbed = rule.merge_func([candidate, neighbor])
        assert "G1" not in absorbed[0].confusion_set
        assert set(absorbed[0].confusion_set) == {"G4", "G5"}

    def test_merge_strips_self_tag_from_confusion_set(self) -> None:
        """Validator + merge interaction — see §5.4 / §5.8."""
        # Both sources carry the self-tag in their confusion_set so the
        # union would re-introduce it; the validator on Lesson must strip.
        n = _make_lesson(
            taxonomy_tag="G1",
            failed_action="click save",
            evidence="distinct evidence A",
            confusion_set=["G1", "G2"],   # bug: self-tag included
        )
        candidate = _make_lesson(
            taxonomy_tag="G1",
            failed_action="click save",
            evidence="brand new evidence with extra tokens xyzzy plover quantum",
            confusion_set=["G1", "G3"],   # bug: self-tag included
        )
        memory = _FakeLessonMemory(hits=_build_hits([n], [0.92]))
        rule = _build_rule(memory)
        rule(candidate)  # routes to UPDATE
        merged = rule.merge_func([candidate, n])
        assert "G1" not in merged[0].confusion_set
        assert set(merged[0].confusion_set) == {"G2", "G3"}


# ---------------------------------------------------------------------------
# Sanity for jaccard helper
# ---------------------------------------------------------------------------


class TestJaccardHelper:
    def test_identical_strings(self) -> None:
        assert jaccard_similarity("click save", "click save") == 1.0

    def test_no_overlap(self) -> None:
        assert jaccard_similarity("foo", "bar") == 0.0

    def test_both_empty(self) -> None:
        assert jaccard_similarity("", "") == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
