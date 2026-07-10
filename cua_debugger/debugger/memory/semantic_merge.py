"""
Semantic, insert-time merge rule for the ``LessonMemory`` pipeline.

Design background — see ``temp/lesson-injector-plan-en.md`` §5.

This module introduces a content-aware alternative to the existing
count-based ``MapMergeRule``.  Every time a new ``Lesson`` enters
``LessonMemory.add``, the router below inspects its bucket-restricted
top-K nearest neighbors (``mem0``-style) and emits one of four actions:

* ``ADD``    — no neighbor crosses the merge threshold.
* ``UPDATE`` — exactly one strongly-similar neighbor; LLM-fuse the pair.
* ``MERGE``  — multiple strongly-similar neighbors; LLM-fuse the whole group.
* ``NOOP``   — the new lesson adds no new information; absorb its
                ``episodic_refs`` into the matching neighbor.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

from langchain_chroma import Chroma

from debugger.utils import ABlockLogger

from .lesson_memory import (
    Lesson,
    LessonMemory,
    MergeRule,
)


# ---------------------------------------------------------------------------
# Light-weight router action types
#
# We use frozen dataclasses (not Enum) because each action carries different
# payloads. Pattern-matching on ``isinstance`` keeps the call sites readable.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddAction:
    """The candidate is novel; keep it in the store as-is."""


@dataclass(frozen=True)
class UpdateAction:
    """Exactly one strongly-similar neighbor exists; LLM-fuse the pair."""

    neighbor: Lesson


@dataclass(frozen=True)
class MergeAction:
    """Multiple strongly-similar neighbors exist; LLM-fuse the whole group."""

    neighbors: Sequence[Lesson]


@dataclass(frozen=True)
class NoopAction:
    """Candidate is a paraphrase of a neighbor; absorb its refs and drop it."""

    absorbed_by: Lesson


RouterAction = AddAction | UpdateAction | MergeAction | NoopAction


# ---------------------------------------------------------------------------
# Surface-form similarity helpers (no LLM, no embeddings)
# ---------------------------------------------------------------------------


_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


def normalize_tokens(text: str) -> set[str]:
    """Lower-case, regex-tokenise, and drop 1-char tokens.

    Used by both the ``failed_action`` Jaccard gate and the
    ``information_gain`` heuristic. Kept tiny and dependency-free
    so it is safe to call inside the ``LessonMemory.add`` write lock.
    """
    if not text:
        return set()
    return {
        token
        for token in _TOKEN_PATTERN.findall(text.lower())
        if len(token) >= 2
    }


def jaccard_similarity(a_text: str, b_text: str) -> float:
    """Symmetric Jaccard over the normalised token sets of two strings."""
    a_tokens = normalize_tokens(a_text)
    b_tokens = normalize_tokens(b_text)
    if not a_tokens and not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def information_gain(candidate: Lesson, neighbor: Lesson) -> int:
    """Count distinct non-stop-word tokens in ``candidate.evidence`` that
    are absent from ``neighbor.evidence``.

    Cheap, LLM-free heuristic used to decide ``UPDATE`` vs ``NOOP`` when
    exactly one strongly-similar neighbor exists.
    """
    cand_tokens = normalize_tokens(candidate.evidence)
    neigh_tokens = normalize_tokens(neighbor.evidence)
    return len(cand_tokens - neigh_tokens)


# ---------------------------------------------------------------------------
# HyDE (Hypothetical Document Embeddings) lookup adapter
#
# Off by default. When enabled, ``SemanticMergeRule`` will ask the LLM to
# draft a hypothetical structured lesson from the candidate, embed *that*,
# and use the embedding for the bucket-restricted ANN lookup.  The
# hypothetical text is never persisted into any ``Lesson`` field.
# ---------------------------------------------------------------------------


class HyDeLessonLookup:
    """Optional HyDE-style query rewriter for dedup lookup.

    The Chroma store already indexes a structured retrieval text
    (``LessonMemory._assemble_retrieval_text``).  Trajectory-grounded
    candidates can be verbose; HyDE bridges that asymmetry by letting an
    LLM draft a compact "hypothetical lesson" before embedding.
    """

    _SYSTEM_PROMPT = (
        "You compress a trajectory-grounded lesson candidate into the same "
        "structured retrieval shape used by the lesson memory: lines starting "
        "with [APP], [TAG], [TITLE], [TRIGGER], [FAILED_ACTION], [LESSON]. "
        "Do not invent content; only restate what the candidate implies. "
        "Output the six lines, nothing else."
    )

    def __init__(self, *, client: Any, model: str, max_tokens: int = 400) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def rewrite(self, candidate: Lesson) -> str:
        """Produce a compact pseudo-document for embedding-only use."""
        user_text = (
            f"[APP] {candidate.app_id or 'unknown'}\n"
            f"[TAG] {candidate.taxonomy_tag}\n"
            f"[TITLE] {candidate.title}\n"
            f"[TRIGGER] {candidate.trigger_condition}\n"
            f"[FAILED_ACTION] {candidate.failed_action}\n"
            f"[LESSON] {candidate.distilled_lesson}\n"
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        parts: list[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip() or user_text


# ---------------------------------------------------------------------------
# The router itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouterThresholds:
    """Three tunable thresholds (see plan §5.3).

    All defaults match the plan's "recommended config" answer (Q3).
    """

    delta_sem: float = 0.85   # cosine similarity gate
    delta_act: float = 0.45   # failed_action Jaccard gate
    epsilon:   int   = 3      # min new evidence tokens to UPDATE rather than NOOP


class MergeRouter:
    """Insert-time router (mem0-style).

    The router queries Chroma directly because ``__call__`` runs inside
    ``LessonMemory.add``'s write lock; re-entering the public lock would
    deadlock the ``RWLock``.  Direct ``Chroma.similarity_search_*`` calls
    are safe in that context because Chroma itself is thread-safe.
    """

    def __init__(
        self,
        *,
        lesson_memory: LessonMemory,
        thresholds: Optional[RouterThresholds] = None,
        top_k: int = 10,
        hyde: Optional[HyDeLessonLookup] = None,
        logger: Optional[ABlockLogger] = None,
    ) -> None:
        self._lesson_memory = lesson_memory
        self._thresholds = thresholds or RouterThresholds()
        self._top_k = top_k
        self._hyde = hyde
        self._logger = logger or ABlockLogger(level="INFO").set_role(role="MergeRouter")

    # -- public API -------------------------------------------------------

    def route(self, candidate: Lesson) -> RouterAction:
        """Choose one of {ADD, UPDATE, MERGE, NOOP} for ``candidate``."""
        neighbors = self._fetch_bucket_neighbors(candidate)
        strong = self._filter_strong_matches(candidate, neighbors)

        if not strong:
            return AddAction()

        if len(strong) == 1:
            one = strong[0]
            gain = information_gain(candidate, one)
            if gain < self._thresholds.epsilon:
                return NoopAction(absorbed_by=one)
            return UpdateAction(neighbor=one)

        return MergeAction(neighbors=tuple(strong))

    # -- internals --------------------------------------------------------

    def _query_text(self, candidate: Lesson) -> str:
        """Pick the embedding text — HyDE rewrite if enabled, else the
        same structured key ``LessonMemory`` uses at index time."""
        if self._hyde is not None:
            try:
                return self._hyde.rewrite(candidate)
            except Exception as exc:  # noqa: BLE001 — log + fall back
                self._logger.warning(
                    message=f"HyDE rewrite failed, falling back to native key: {exc}"
                )
        return LessonMemory._assemble_retrieval_text(candidate)

    def _fetch_bucket_neighbors(self, candidate: Lesson) -> List[Lesson]:
        """Top-K Chroma neighbors restricted to the candidate's bucket.

        ``where`` filter pins ``(taxonomy_tag, app_id)`` so cross-bucket
        merges are impossible at the storage layer.  The candidate itself
        is excluded if it appears (it was added immediately before this
        rule runs).
        """
        database: Chroma = self._lesson_memory.database

        where_clauses: list[dict[str, Any]] = [
            {"taxonomy_tag": candidate.taxonomy_tag},
        ]
        if candidate.app_id is not None:
            where_clauses.append({"app_id": candidate.app_id})
        where_filter = (
            where_clauses[0] if len(where_clauses) == 1
            else {"$and": where_clauses}
        )

        query_text = self._query_text(candidate)
        try:
            raw_hits = database.similarity_search_with_relevance_scores(
                query_text,
                k=self._top_k,
                filter=where_filter,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                message=f"bucket ANN lookup failed: {exc}; routing as ADD"
            )
            return []

        own_id = str(candidate.id)
        neighbors: List[Lesson] = []
        self._last_scores: dict[str, float] = {}
        for document, score in raw_hits:
            neighbor_id = (document.metadata or {}).get("id")
            if neighbor_id == own_id:
                continue
            try:
                neighbor = Lesson.model_validate(document.metadata)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    message=f"skipping malformed neighbor metadata: {exc}"
                )
                continue
            self._last_scores[str(neighbor.id)] = score
            neighbors.append(neighbor)
        return neighbors

    def _filter_strong_matches(
        self,
        candidate: Lesson,
        neighbors: Iterable[Lesson],
    ) -> List[Lesson]:
        """Keep only neighbors that pass *both* gates: cosine and Jaccard."""
        strong: List[Lesson] = []
        for neighbor in neighbors:
            cosine_score = self._last_scores.get(str(neighbor.id), 0.0)
            if cosine_score < self._thresholds.delta_sem:
                continue
            action_jaccard = jaccard_similarity(
                candidate.failed_action, neighbor.failed_action,
            )
            if action_jaccard < self._thresholds.delta_act:
                continue
            strong.append(neighbor)
        return strong


# ---------------------------------------------------------------------------
# SemanticMergeRule — the bound rule
# ---------------------------------------------------------------------------


@dataclass
class _MergedFields:
    """Internal staging dict for the per-field merge step (§5.4)."""

    fields: dict[str, Any] = field(default_factory=dict)


class SemanticMergeRule(MergeRule):
    """A ``MergeRule`` that uses ``MergeRouter`` to decide what to consolidate.

    The rule plugs into ``LessonMemory.add`` via
    ``lesson_memory.bind_merge_rule(...)``.  On every ``add()`` it:

    1. Runs the router on the freshly inserted candidate.
    2. Returns the list of lesson UUIDs the host should pop so that
       ``merge_func`` can produce the consolidated record(s).
    """

    def __init__(
        self,
        *,
        lesson_memory: LessonMemory,
        client: Any,
        model: str,
        thresholds: Optional[RouterThresholds] = None,
        top_k: int = 10,
        hyde: Optional[HyDeLessonLookup] = None,
        max_tokens: int = 1500,
        logger: Optional[ABlockLogger] = None,
    ) -> None:
        self._lesson_memory = lesson_memory
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._logger = logger or ABlockLogger(level="INFO").set_role(
            role="SemanticMergeRule",
        )
        self._router = MergeRouter(
            lesson_memory=lesson_memory,
            thresholds=thresholds,
            top_k=top_k,
            hyde=hyde,
            logger=self._logger,
        )
        # Action recorded during ``__call__``; consumed by ``merge_func``.
        self._pending_action: dict[uuid.UUID, RouterAction] = {}

    # -- MergeRule interface ---------------------------------------------

    def __call__(self, lesson: Lesson) -> List[uuid.UUID]:
        """Decide the action for ``lesson`` and return the IDs to pop."""
        action = self._router.route(lesson)
        self._pending_action[lesson.id] = action

        if isinstance(action, AddAction):
            return []

        if isinstance(action, NoopAction):
            # Bucket = [candidate, absorbed_by]. ``merge_func`` will re-emit
            # the neighbor with the candidate's refs appended.
            return [lesson.id, action.absorbed_by.id]

        if isinstance(action, UpdateAction):
            return [lesson.id, action.neighbor.id]

        if isinstance(action, MergeAction):
            return [lesson.id] + [n.id for n in action.neighbors]

        raise TypeError(f"unexpected router action: {action!r}")

    def merge_func(self, bucket: List[Lesson]) -> List[Lesson]:
        """Field-by-field consolidate the popped bucket per §5.4."""
        if not bucket:
            return []

        # The candidate is always the first element passed to ``__call__``
        # so it sits at the front of every bucket we return from there.
        candidate = bucket[0]
        action = self._pending_action.pop(candidate.id, None)

        if isinstance(action, NoopAction):
            # Carry over the absorbed-by neighbor verbatim, but with the
            # candidate's ``episodic_refs`` appended (set-union; preserves
            # Text2Mem provenance invariant).
            neighbor = bucket[1]
            absorbed = self._absorb_into_neighbor(candidate, neighbor)
            self._logger.debug(
                message=(
                    f"NOOP: candidate {str(candidate.id)[:8]} absorbed into "
                    f"{str(neighbor.id)[:8]} "
                    f"(taxonomy_tag={candidate.taxonomy_tag}, "
                    f"app_id={candidate.app_id})"
                ),
            )
            return [absorbed]

        # UPDATE / MERGE / (unexpected fallback): LLM-fuse the bucket.
        merged_fields = self._build_merged_fields(bucket)
        pre_set = set(merged_fields.get("confusion_set", []))
        merged = Lesson(**merged_fields)
        stripped = pre_set - set(merged.confusion_set)
        if stripped:
            self._logger.debug(
                message=(
                    "confusion_set self-tag stripped on merge: "
                    f"{sorted(stripped)} "
                    f"(taxonomy_tag={merged.taxonomy_tag}, "
                    f"lesson_id={str(merged.id)[:8]})"
                ),
            )
        return [merged]

    # -- field-rule helpers ----------------------------------------------

    def _absorb_into_neighbor(
        self,
        candidate: Lesson,
        neighbor: Lesson,
    ) -> Lesson:
        """NOOP path — keep the neighbor's text, union the refs+confusion.

        Rebuilds via ``Lesson(**dict)`` rather than ``model_copy`` so the
        ``confusion_set`` self-tag validator (§5.8) runs on the absorbed
        record.  ``model_copy(update=...)`` in Pydantic v2 explicitly
        skips ``after`` validators, which would let a stale self-tag slip
        through here.
        """
        union_refs = sorted({*neighbor.episodic_refs, *candidate.episodic_refs})
        union_confusion = sorted({*neighbor.confusion_set, *candidate.confusion_set})
        absorbed_fields = neighbor.model_dump(mode="python")
        absorbed_fields["id"] = uuid.uuid4()
        absorbed_fields["episodic_refs"] = union_refs
        absorbed_fields["confusion_set"] = union_confusion
        return Lesson(**absorbed_fields)

    def _build_merged_fields(self, bucket: List[Lesson]) -> dict[str, Any]:
        """Apply the §5.4 per-field rules and call the LLM for free-text fields."""
        # Deterministic fields ------------------------------------------
        taxonomy_tag = bucket[0].taxonomy_tag
        app_id = bucket[0].app_id

        union_refs = sorted({ref for lesson in bucket for ref in lesson.episodic_refs})
        union_confusion = sorted(
            {tag for lesson in bucket for tag in lesson.confusion_set}
        )

        # Highest-support ``failed_action`` (ties broken by shortest)
        failed_action = self._pick_highest_support(
            (lesson.failed_action for lesson in bucket),
        )

        # Most-recent ``corrected_action`` (newer fix supersedes older)
        corrected_action = self._pick_most_recent(
            bucket,
            attr="corrected_action",
        )

        # Free-text fields — single LLM call -----------------------------
        fused = self._llm_fuse_free_text(bucket)

        merged: dict[str, Any] = {
            "taxonomy_tag": taxonomy_tag,
            "app_id": app_id,
            "title": fused.get("title", bucket[0].title),
            "distilled_lesson": fused.get("distilled_lesson", bucket[0].distilled_lesson),
            "trigger_condition": fused.get("trigger_condition", bucket[0].trigger_condition),
            "failed_action": failed_action,
            "corrected_action": corrected_action,
            "distinguishing_feature": fused.get(
                "distinguishing_feature", bucket[0].distinguishing_feature,
            ),
            "evidence": fused.get("evidence", bucket[0].evidence),
            "confusion_set": union_confusion,
            "episodic_refs": union_refs,
        }
        return merged

    @staticmethod
    def _pick_highest_support(candidates: Iterable[str]) -> str:
        """Return the most-supported variant; ties broken by shortest."""
        from collections import Counter
        counts = Counter(c for c in candidates if c)
        if not counts:
            return ""
        max_count = max(counts.values())
        winners = [c for c, n in counts.items() if n == max_count]
        return min(winners, key=len)

    @staticmethod
    def _pick_most_recent(bucket: List[Lesson], *, attr: str) -> str:
        """Return ``getattr(lesson, attr)`` from the most recently created lesson."""
        most_recent = max(bucket, key=lambda lesson: lesson.created_at)
        return getattr(most_recent, attr, "") or ""

    # -- LLM call ---------------------------------------------------------

    _FUSE_SYSTEM_PROMPT = (
        "You are consolidating a small bucket of agent debugging lessons that "
        "all share the same taxonomy_tag and app_id and were already detected "
        "by a semantic-similarity router as describing the same failure pattern. "
        "Produce ONE generalized record that strips scenario-specific noise.\n\n"
        "Output ONLY a single JSON object (no markdown fences, no prose, "
        "no trailing text) with EXACTLY these keys:\n"
        "  title, distilled_lesson, trigger_condition, distinguishing_feature, evidence\n"
        "Each value must be a string. Do NOT cut the output halfway: the JSON "
        "object MUST close with `}`, every field except the last MUST end with "
        "`,`, and string values MUST NOT contain invalid / unparseable characters."
    )

    def _llm_fuse_free_text(self, bucket: List[Lesson]) -> dict[str, str]:
        """Single LLM call to fuse the free-text fields across the bucket.

        On any failure (LLM error, JSON parse error) we degrade gracefully
        by returning the first lesson's free-text fields verbatim.  The
        merge still proceeds with deterministic fields intact.
        """
        import json

        payload_items: list[dict[str, str]] = []
        for lesson in bucket:
            payload_items.append({
                "title": lesson.title,
                "distilled_lesson": lesson.distilled_lesson,
                "trigger_condition": lesson.trigger_condition,
                "distinguishing_feature": lesson.distinguishing_feature,
                "evidence": lesson.evidence,
            })
        user_text = json.dumps(payload_items, ensure_ascii=False, indent=2)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._FUSE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_text}],
            )
            parts: list[str] = []
            for block in response.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            raw = "".join(parts).strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                message=f"LLM fuse failed, keeping first lesson's text: {exc}",
            )
            first = bucket[0]
            return {
                "title": first.title,
                "distilled_lesson": first.distilled_lesson,
                "trigger_condition": first.trigger_condition,
                "distinguishing_feature": first.distinguishing_feature,
                "evidence": first.evidence,
            }