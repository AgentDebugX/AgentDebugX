"""
OSWorld trajectory debugger package.

Public API:
    from debugger import run_agent, load_trajectory, build_output, print_summary
    from debugger import ingest, IngestionResult, Step
    from debugger import run_rca, RCAResult
    from debugger import TaxonomyTag, tag_from_rca, soft_tag_candidates
"""

from .trajectory import load_trajectory, load_normalized_trajectory, find_step, image_block
from .output import build_output, print_summary
from .taxonomy import (
    TAXONOMY_CATEGORIES, TAXONOMY_DEFINITIONS,
    TAXONOMY_SUBTYPES, ALL_SUBTYPES, SUBTYPE_DEFINITIONS,
    SUBTYPE_TO_CATEGORY, V1_TO_V2_CATEGORY,
)
from .prompts import SYSTEM_PROMPT
from .ingester import *
from .tagger import TaxonomyTag, tag_from_rca, soft_tag_candidates


def __getattr__(name):
    if name == "run_agent":
        from .agent import run_agent
        globals()["run_agent"] = run_agent
        return run_agent
    if name in {"TOOLS", "RCA_TOOLS"}:
        from .tools import TOOLS, RCA_TOOLS
        globals()["TOOLS"] = TOOLS
        globals()["RCA_TOOLS"] = RCA_TOOLS
        return globals()[name]
    if name in {"run_rca", "RCAResult"}:
        from .rca import run_rca, RCAResult
        globals()["run_rca"] = run_rca
        globals()["RCAResult"] = RCAResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "run_agent",
    "load_trajectory",
    "load_normalized_trajectory",
    "find_step",
    "image_block",
    "build_output",
    "print_summary",
    "TAXONOMY_CATEGORIES",
    "TAXONOMY_DEFINITIONS",
    "TAXONOMY_SUBTYPES",
    "ALL_SUBTYPES",
    "SUBTYPE_DEFINITIONS",
    "SUBTYPE_TO_CATEGORY",
    "V1_TO_V2_CATEGORY",
    "TOOLS",
    "RCA_TOOLS",
    "SYSTEM_PROMPT",
    "IngestionResult",
    "Step",
    "run_rca",
    "RCAResult",
    "TaxonomyTag",
    "tag_from_rca",
    "soft_tag_candidates",
]
