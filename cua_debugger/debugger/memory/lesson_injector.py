"""
Lesson injector — builds the taxonomy-indexed HTML reference table that the
RCA system prompt prepends to its user message.

Design background — see ``temp/lesson-injector-plan-en.md`` §§3–4, §11.1.

Public API:

* ``LessonInjector.build(app_id=...)`` returns the full HTML/markdown table.
* ``LessonInjector.build_row(taxonomy_tag, app_id=...)`` returns just one row;
  useful for tests.

Internally the injector composes three small collaborators:

* ``LessonSelector`` (interface) → ``CompositeSelector`` (default)
  decides which Lesson is the representative for a given bucket.
* ``TaxonomySheetRenderer`` (interface) → ``HtmlTaxonomySheetRenderer`` (default)
  serialises the chosen representatives into the markdown/HTML table.

Each helper is a small class with no module-level state.  Threshold and
weight defaults live on ``CompositeWeights``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Protocol, Sequence

from debugger.taxonomy import (
    SUBTYPE_DEFINITIONS,
    SUBTYPE_TO_CATEGORY,
    TAXONOMY_CATEGORIES,
    TAXONOMY_DEFINITIONS,
    TAXONOMY_SUBTYPES,
)

from .lesson_memory import Lesson, LessonMemory


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_NO_LESSON_TOKEN: str = "<NO-LESSON>"
_CITATION_KEY_LENGTH: int = 8  # short-UUID length displayed in [L:<…>] prefix


# Single-letter / two-letter category abbreviations used in the table header.
# Derived once from ``TAXONOMY_CATEGORIES`` so the order matches the canonical
# taxonomy ordering everywhere.
_CATEGORY_PREFIX_MAP: dict[str, str] = {
    "Perception":                "P",
    "Grounding & Interaction":   "G",
    "Task Reasoning & Control":  "R",
    "External / System":         "S",
    "Infeasible Task":           "IF",
}


# ---------------------------------------------------------------------------
# Selector — picks one representative per bucket
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositeWeights:
    """Default scoring weights for ``CompositeSelector`` (plan §4)."""

    alpha_recency:        float = 0.4
    beta_coverage:        float = 0.4
    gamma_information:    float = 0.2
    tau_half_days:        float = 30.0
    confusion_bonus_step: float = 0.25


class LessonSelector(Protocol):
    """Strategy interface — pick the best representative from a bucket."""

    def pick(self, bucket: Sequence[Lesson]) -> Optional[Lesson]:
        ...

    def rank(self, bucket: Sequence[Lesson]) -> List[Lesson]:
        """Return ``bucket`` ordered best-first (used by exploration tools)."""
        ...


class CompositeSelector:
    """Default selector: recency × coverage × informativeness (plan §4).

    Score formula::

        recency(L)      = exp(-Δdays(L) / τ_half)
        coverage(L)     = |episodic_refs(L)|
        informativeness = 1 + γ_step · |confusion_set(L)|
        score(L)        = α · recency
                        + β · log(1 + coverage)
                        + γ · informativeness
    """

    def __init__(self, *, weights: Optional[CompositeWeights] = None) -> None:
        self._weights = weights or CompositeWeights()

    def pick(self, bucket: Sequence[Lesson]) -> Optional[Lesson]:
        if not bucket:
            return None
        return self.rank(bucket)[0]

    def rank(self, bucket: Sequence[Lesson]) -> List[Lesson]:
        # Stable sort by descending score, ties broken by ``created_at``
        # (more recent first) for determinism.
        scored = [(self._score(lesson), lesson.created_at, lesson) for lesson in bucket]
        scored.sort(key=lambda triple: (triple[0], triple[1]), reverse=True)
        return [lesson for _, _, lesson in scored]

    # -- internals --------------------------------------------------------

    def _score(self, lesson: Lesson) -> float:
        w = self._weights
        recency = self._recency_score(lesson, tau_half_days=w.tau_half_days)
        coverage = math.log1p(len(lesson.episodic_refs))
        informativeness = 1.0 + w.confusion_bonus_step * len(lesson.confusion_set)
        return (
            w.alpha_recency      * recency
            + w.beta_coverage    * coverage
            + w.gamma_information * informativeness
        )

    @staticmethod
    def _recency_score(lesson: Lesson, *, tau_half_days: float) -> float:
        """Half-life decay over the lesson's ``created_at`` timestamp."""
        try:
            stamp = datetime.fromisoformat(lesson.created_at)
        except (TypeError, ValueError):
            return 0.0
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta_days = max(0.0, (now - stamp).total_seconds() / 86_400.0)
        return math.exp(-delta_days / max(tau_half_days, 1e-9))


# ---------------------------------------------------------------------------
# Renderer — serialises selections into the markdown/HTML table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxonomyRow:
    """One row of the reference table — a (category, taxonomy, lesson) triple."""

    category_name:   str
    category_prefix: str            # e.g. "R" or "IF"
    category_def:    str
    taxonomy_tag:    str            # e.g. "R4"
    taxonomy_def:    str            # e.g. "Inefficient / Redundant Strategy — …"
    lesson:          Optional[Lesson]


class TaxonomySheetRenderer(Protocol):
    """Strategy interface — turn ``[TaxonomyRow, …]`` into a string."""

    def render(self, rows: Sequence[TaxonomyRow]) -> str:
        ...


class HtmlTaxonomySheetRenderer:
    """Default renderer: HTML table with ``rowspan``-merged category cells.

    Output is markdown-safe (most parsers pass HTML through verbatim) and
    Claude reliably parses HTML tables, so this is the canonical injection
    format used by the RCA prompt.
    """

    def render(self, rows: Sequence[TaxonomyRow]) -> str:
        body_blocks: list[str] = []
        # Group by category to emit one ``rowspan`` per category.
        for category_name, category_rows in self._iter_category_groups(rows):
            body_blocks.append(self._render_category_block(category_name, category_rows))
        return self._wrap_table("\n".join(body_blocks))

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _iter_category_groups(
        rows: Sequence[TaxonomyRow],
    ) -> Iterable[tuple[str, list[TaxonomyRow]]]:
        """Yield ``(category_name, [rows])`` preserving the canonical order."""
        by_category: dict[str, list[TaxonomyRow]] = {}
        for row in rows:
            by_category.setdefault(row.category_name, []).append(row)
        for category_name in TAXONOMY_CATEGORIES:
            if category_name in by_category:
                yield category_name, by_category[category_name]

    @classmethod
    def _render_category_block(
        cls,
        category_name: str,
        rows: list[TaxonomyRow],
    ) -> str:
        """One ``<tr>...</tr>`` per taxonomy with the first row merging
        the category cells via ``rowspan``."""
        span = len(rows)
        prefix = rows[0].category_prefix
        category_def_html = cls._html_escape(rows[0].category_def)

        out_lines: list[str] = []
        for index, row in enumerate(rows):
            taxonomy_label = cls._html_escape(
                f"{row.taxonomy_tag} ({cls._strip_dash_head(row.taxonomy_def)})"
            )
            taxonomy_def_html = cls._html_escape(cls._strip_tag_head(row.taxonomy_def))
            example_cell = cls._render_example_cell(row.lesson)

            if index == 0:
                merged_category_cells = (
                    f'  <td rowspan="{span}"><b>{prefix} ({cls._html_escape(category_name)})</b></td>\n'
                    f'  <td rowspan="{span}">{category_def_html}</td>\n'
                )
            else:
                merged_category_cells = ""

            out_lines.append(
                "<tr>\n"
                + merged_category_cells
                + f"  <td>{taxonomy_label}</td>\n"
                + f"  <td>{taxonomy_def_html}</td>\n"
                + f"  <td>{example_cell}</td>\n"
                + "</tr>"
            )
        return "\n".join(out_lines)

    @classmethod
    def _render_example_cell(cls, lesson: Optional[Lesson]) -> str:
        """Selected lesson formatted as ``[L:<short>] …to_prompt()…``,
        or the literal ``<NO-LESSON>`` token."""
        if lesson is None:
            # HTML-escape the literal token so the table renders the
            # angle brackets visibly rather than parsing them as a tag.
            return cls._html_escape(_NO_LESSON_TOKEN)
        short_id = str(lesson.id).split("-")[0][:_CITATION_KEY_LENGTH]
        # ``<pre>`` keeps the multi-line ``to_prompt()`` block readable inside
        # a table cell without HTML-escaping the markdown.  Citation prefix
        # uses bold markdown so it stands out to the RCA model.
        prompt_body = cls._html_escape(lesson.to_prompt())
        return (
            f"<b>[L:{short_id}]</b><br>\n"
            f"<pre>{prompt_body}</pre>"
        )

    @staticmethod
    def _wrap_table(body: str) -> str:
        header = (
            "<table>\n"
            "<thead>\n"
            "<tr>"
            "<th>Category</th>"
            "<th>Category Definition</th>"
            "<th>Taxonomy</th>"
            "<th>Definition</th>"
            "<th>Example Lesson</th>"
            "</tr>\n"
            "</thead>\n"
            "<tbody>\n"
        )
        return header + body + "\n</tbody>\n</table>"

    @staticmethod
    def _html_escape(text: str) -> str:
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _strip_dash_head(definition: str) -> str:
        """Return the part before the long dash in ``SUBTYPE_DEFINITIONS``."""
        # Definitions are of shape ``"<Short Name> — <long sentence>"``
        head, _sep, _tail = definition.partition("—")
        return head.strip().rstrip(":")

    @staticmethod
    def _strip_tag_head(definition: str) -> str:
        """Return the part after the long dash in ``SUBTYPE_DEFINITIONS``."""
        _head, _sep, tail = definition.partition("—")
        return (tail or definition).strip()


# ---------------------------------------------------------------------------
# The injector itself
# ---------------------------------------------------------------------------


class LessonInjector:
    """Builds a taxonomy-indexed reference table from a ``LessonMemory``.

    Usage::

        injector = LessonInjector(lesson_memory=mem)
        sheet = injector.build(app_id="chrome")        # Mode B (default)
        sheet_global = injector.build(app_id=None)     # Mode A

    Both modes obey the plan's fallback chain when ``app_id`` is provided:

        app-scoped lessons → global pool → ``<NO-LESSON>``
    """

    def __init__(
        self,
        *,
        lesson_memory: LessonMemory,
        selector: Optional[LessonSelector] = None,
        renderer: Optional[TaxonomySheetRenderer] = None,
    ) -> None:
        self._lesson_memory = lesson_memory
        self._selector = selector or CompositeSelector()
        self._renderer = renderer or HtmlTaxonomySheetRenderer()

    # -- public API -------------------------------------------------------

    def build(self, *, app_id: Optional[str] = None) -> str:
        """Build the full HTML table covering every taxonomy code."""
        rows = [
            self._build_row_internal(tag, app_id=app_id)
            for tag in self._all_taxonomy_tags_in_canonical_order()
        ]
        return self._renderer.render(rows)

    def build_row(self, taxonomy_tag: str, *, app_id: Optional[str] = None) -> str:
        """Build the markdown for a single taxonomy row — handy for tests."""
        row = self._build_row_internal(taxonomy_tag, app_id=app_id)
        return self._renderer.render([row])

    # -- internals --------------------------------------------------------

    @staticmethod
    def _all_taxonomy_tags_in_canonical_order() -> list[str]:
        ordered: list[str] = []
        for category_name in TAXONOMY_CATEGORIES:
            ordered.extend(TAXONOMY_SUBTYPES[category_name])
        return ordered

    def _build_row_internal(
        self,
        taxonomy_tag: str,
        *,
        app_id: Optional[str],
    ) -> TaxonomyRow:
        category_name = SUBTYPE_TO_CATEGORY.get(taxonomy_tag, "")
        category_def = TAXONOMY_DEFINITIONS.get(category_name, "")
        category_prefix = _CATEGORY_PREFIX_MAP.get(category_name, "")
        taxonomy_def = SUBTYPE_DEFINITIONS.get(taxonomy_tag, "")
        lesson = self._pick_with_fallback(taxonomy_tag, app_id=app_id)
        return TaxonomyRow(
            category_name=category_name,
            category_prefix=category_prefix,
            category_def=category_def,
            taxonomy_tag=taxonomy_tag,
            taxonomy_def=taxonomy_def,
            lesson=lesson,
        )

    def _pick_with_fallback(
        self,
        taxonomy_tag: str,
        *,
        app_id: Optional[str],
    ) -> Optional[Lesson]:
        """Mode B with global fallback (plan §11.1).

        Step 1 — try the bucket restricted to ``app_id``.
        Step 2 — fall back to the global bucket.
        Step 3 — return ``None`` (renderer emits ``<NO-LESSON>``).

        When ``app_id`` is ``None`` Step 1 is skipped (== Mode A).
        """
        if app_id is not None:
            app_bucket = self._collect_bucket(taxonomy_tag, app_id=app_id)
            chosen = self._selector.pick(app_bucket)
            if chosen is not None:
                return chosen
        global_bucket = self._collect_bucket(taxonomy_tag, app_id=None)
        return self._selector.pick(global_bucket)

    def _collect_bucket(
        self,
        taxonomy_tag: str,
        *,
        app_id: Optional[str],
    ) -> List[Lesson]:
        """Iterate the full LessonMemory and keep matches.

        We intentionally scan rather than use Chroma's ``where`` filter for
        two reasons:

        1. ``LessonMemory`` already exposes ``__iter__`` under a read lock,
           which keeps the contract clean.
        2. The lesson stores in practice are small (tens-to-thousands of
           records); a linear scan is comfortably cheaper than spinning up
           an ANN query when we don't need similarity at all.
        """
        bucket: List[Lesson] = []
        for lesson in self._lesson_memory:
            if lesson.taxonomy_tag != taxonomy_tag:
                continue
            if app_id is not None and lesson.app_id != app_id:
                continue
            bucket.append(lesson)
        return bucket