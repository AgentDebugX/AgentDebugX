"""
Taxonomy Tagger v2 for trajectory debugger.

Provides two pure-mapping interfaces (no LLM calls):

1. tag_from_rca(rca_result) -> TaxonomyTag
   Extract and validate the taxonomy_tag from an RCAResult.
   Accepts both category names ("Perception") and sub-type codes ("P1").

2. soft_tag_candidates(action_type, app_id, visual_delta) -> list[tuple[str, float]]
   Generate ranked (taxonomy, probability) candidates from observable step
   features, for use in Phase 2 re-ranking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .taxonomy import (
    TAXONOMY_CATEGORIES,
    ALL_SUBTYPES,
    SUBTYPE_TO_CATEGORY,
    V1_TO_V2_CATEGORY,
)

if TYPE_CHECKING:
    from .rca import RCAResult

# ---------------------------------------------------------------------------
# Type alias for a validated taxonomy tag string
# ---------------------------------------------------------------------------

TaxonomyTag = str  # category name or sub-type code

_VALID_TAGS: frozenset[str] = frozenset(TAXONOMY_CATEGORIES + ALL_SUBTYPES)

# Shorthand references for v2 categories
_PERCEPTION = "Perception"
_GROUNDING  = "Grounding & Interaction"
_REASONING  = "Task Reasoning & Control"
_SYSTEM     = "External / System"

# ---------------------------------------------------------------------------
# 1. tag_from_rca
# ---------------------------------------------------------------------------

def tag_from_rca(rca_result: "RCAResult") -> TaxonomyTag:
    """
    Extract and validate the taxonomy tag from an RCAResult.

    Accepts v2 category names, sub-type codes (P1, G2, R10, etc.),
    and migrates v1 category names automatically.

    Returns:
        The taxonomy_tag string (validated).

    Raises:
        ValueError: If taxonomy_tag is not recognized.
    """
    tag = rca_result.taxonomy_tag

    # Accept v2 tags directly
    if tag in _VALID_TAGS:
        return tag

    # Migrate v1 category names to v2
    if tag in V1_TO_V2_CATEGORY:
        return V1_TO_V2_CATEGORY[tag]

    valid = ", ".join(f'"{t}"' for t in TAXONOMY_CATEGORIES + ALL_SUBTYPES[:8])
    raise ValueError(
        f"taxonomy_tag {tag!r} is not a valid TaxonomyTag. "
        f"Valid values include: {valid}, ..."
    )


# ---------------------------------------------------------------------------
# 2. soft_tag_candidates — heuristic mapping, no LLM
# ---------------------------------------------------------------------------

# Base weights per category (uniform start)
_BASE: dict[str, float] = {t: 1.0 for t in TAXONOMY_CATEGORIES}

# Action-type -> per-category additive weight adjustments.
_ACTION_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "click": {
        _GROUNDING:  2.5,
        _PERCEPTION: 1.5,
    },
    "double_click": {
        _GROUNDING:  2.5,
        _PERCEPTION: 1.0,
    },
    "right_click": {
        _GROUNDING:  2.0,
        _PERCEPTION: 0.8,
    },
    "drag": {
        _GROUNDING:  3.0,
        _PERCEPTION: 0.5,
    },
    "type": {
        _REASONING: 2.5,
    },
    "key": {
        _REASONING: 2.0,
    },
    "hotkey": {
        _REASONING: 2.0,
    },
    "scroll": {
        _GROUNDING: 2.0,
    },
    "screenshot": {
        _PERCEPTION: 2.5,
    },
    "finish": {
        _REASONING: 3.0,
    },
    "move": {
        _GROUNDING: 1.5,
    },
    "wait": {
        _SYSTEM:    2.0,
        _REASONING: 1.0,
    },
}

# App-id -> per-category additive weight adjustments.
_APP_ID_WEIGHTS: dict[str, dict[str, float]] = {
    "firefox":     {_GROUNDING: 1.0, _PERCEPTION: 0.5},
    "chromium":    {_GROUNDING: 1.0, _PERCEPTION: 0.5},
    "chrome":      {_GROUNDING: 1.0, _PERCEPTION: 0.5},
    "libreoffice": {_REASONING: 1.5},
    "writer":      {_REASONING: 1.0},
    "calc":        {_REASONING: 1.0},
    "impress":     {_REASONING: 0.8, _GROUNDING: 0.5},
    "vscode":      {_REASONING: 1.0},
    "code":        {_REASONING: 1.0},
    "terminal":    {_REASONING: 1.5},
    "bash":        {_REASONING: 1.5},
    "gimp":        {_PERCEPTION: 1.5, _GROUNDING: 1.0},
    "evince":      {_GROUNDING: 0.5, _PERCEPTION: 0.8},
    "nautilus":    {_GROUNDING: 1.0},
    "thunar":      {_GROUNDING: 1.0},
    "gedit":       {_REASONING: 0.5},
}


def soft_tag_candidates(
    action_type: str,
    app_id: str,
    visual_delta: Optional[float],
) -> list[tuple[str, float]]:
    """
    Generate ranked taxonomy candidates from observable step features.

    Uses purely heuristic weight tables — no LLM calls.

    Args:
        action_type:  The action type string for the step (e.g., "click",
                      "type", "scroll"). Case-insensitive.
        app_id:       The application identifier (e.g., "firefox",
                      "libreoffice"). Case-insensitive.
        visual_delta: Normalised visual change score between the before- and
                      after-screenshots (0.0 = no change, 1.0 = completely
                      different). Pass None when unavailable.

    Returns:
        List of (taxonomy_category, probability) tuples, sorted by probability
        descending. All probabilities sum to 1.0.
    """
    scores: dict[str, float] = dict(_BASE)

    # --- action_type adjustments ---
    action_key = (action_type or "").lower().strip()
    for tag, weight in _ACTION_TYPE_WEIGHTS.get(action_key, {}).items():
        scores[tag] = scores[tag] + weight

    # --- app_id adjustments ---
    app_key = (app_id or "").lower().strip()
    for tag, weight in _APP_ID_WEIGHTS.get(app_key, {}).items():
        scores[tag] = scores[tag] + weight

    # --- visual_delta adjustments ---
    if visual_delta is not None:
        delta = float(visual_delta)
        if delta <= 0.02:
            # No visible change -> likely failed to detect action outcome
            scores[_REASONING] += 2.5
        elif delta >= 0.40:
            # Dramatic unexpected change -> possible system/env issue
            scores[_SYSTEM]    += 2.0
            scores[_GROUNDING] += 0.5
        else:
            # Moderate change -> slight grounding bias
            scores[_GROUNDING] += 0.3

    # --- normalise to probability distribution ---
    total = sum(scores.values())
    normalised = [(tag, scores[tag] / total) for tag in TAXONOMY_CATEGORIES]
    normalised.sort(key=lambda x: x[1], reverse=True)
    return normalised
