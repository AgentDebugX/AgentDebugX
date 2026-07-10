"""Accuracy evaluation module.

Pure, UI-free computation of RCA-vs-human-annotation metrics. Used by the
Streamlit app, the analysis scripts, and any ad-hoc ablation comparison.
"""

from debugger.eval.accuracy import (
    compute_accuracy,
    compute_overall,
    compute_per_annotator,
    compute_step_metrics,
    compute_tag_metrics,
    get_category,
    load_pairs,
    normalize_tag,
    quick_acc,
)

__all__ = [
    "compute_accuracy",
    "compute_overall",
    "compute_per_annotator",
    "compute_step_metrics",
    "compute_tag_metrics",
    "get_category",
    "load_pairs",
    "normalize_tag",
    "quick_acc",
]
