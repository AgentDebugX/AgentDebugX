"""
Accuracy evaluation for the GUI Agent Debugger.

Compares LLM RCA predictions against human-annotated ground truth and
computes multi-level accuracy metrics. Pure backend — no UI or plotting.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def normalize_tag(tag: str) -> str:
    """Normalize legacy/free-text labels into the paper taxonomy namespace."""
    tag = str(tag or "").strip()
    if tag.lower() == "infeasible task":
        return "IF"
    return tag


def get_category(tag: str) -> str:
    """Extract the category prefix from a taxonomy tag (e.g., 'G1' -> 'G', 'IF2' -> 'IF')."""
    tag = normalize_tag(tag)
    m = re.match(r"[A-Z]+", tag)
    return m.group() if m else tag


def _safe_div(num: float, den: float) -> float:
    return num / den if den > 0 else 0.0


def _valid_human(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and record.get("root_error_step") is not None
        and bool(normalize_tag(record.get("taxonomy_tag")))
    )


def _select_human(data: dict, annotation_policy: str) -> dict | None:
    """Select the human reference label for the requested annotation policy."""
    final_decision = data.get("final_decision")
    human_values = data.get("human_values")
    if isinstance(human_values, dict):
        human_values = [human_values]
    if not isinstance(human_values, list):
        human_values = []
    valid_humans = [h for h in human_values if _valid_human(h)]

    if annotation_policy == "final_decision":
        return final_decision if _valid_human(final_decision) else None
    if annotation_policy == "final_or_single":
        if _valid_human(final_decision):
            return final_decision
        if len(valid_humans) == 1:
            return valid_humans[0]
        return None
    if annotation_policy == "first_human":
        return valid_humans[0] if valid_humans else None
    raise ValueError(f"Unknown annotation_policy: {annotation_policy}")


def load_pairs(
    trial_dir: str | Path,
    annotation_policy: str = "final_or_single",
) -> tuple[list[dict], dict]:
    """Load and pair RCA predictions with human annotations.

    Returns (pairs, metadata) where each pair is a dict with keys:
        task_id, llm_tag, llm_step, llm_confidence, human_tag, human_step, annotator
    and metadata has debugger_model, agent_model, skipped_no_human.
    """
    trial_dir = Path(trial_dir)
    rca_dir = trial_dir / "rca"
    ann_dir = trial_dir / "annotations"
    nested_layout = False
    if not ann_dir.exists():
        # Nested layout fallback: annotations live one level up at the agent-trial dir.
        # Flat layout (legacy 50-step / 15-step trials) keeps the original sibling path.
        ann_dir = trial_dir.parent / "annotations"
        nested_layout = ann_dir.exists()

    rca_by_id: dict[str, dict] = {}
    debugger_model = None
    if rca_dir.exists():
        for f in rca_dir.glob("rca_*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            tid = data.get("task_id", "")
            if tid:
                rca_by_id[tid] = data
                if debugger_model is None:
                    debugger_model = data.get("model", "unknown")

    ann_by_id: dict[str, tuple[dict, dict]] = {}
    if ann_dir.exists():
        for f in ann_dir.glob("human_*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            tid = data.get("task_id", "")
            human = _select_human(data, annotation_policy)
            if tid and human:
                ann_by_id[tid] = (data, human)

    pairs = []
    common_ids = set(rca_by_id.keys()) & set(ann_by_id.keys())
    skipped = len(rca_by_id) - len(common_ids)

    for tid in sorted(common_ids):
        rca = rca_by_id[tid]
        ann, human = ann_by_id[tid]
        pairs.append({
            "task_id": tid,
            "llm_tag": normalize_tag(rca.get("taxonomy_tag", "")),
            "llm_step": int(rca.get("root_error_step", 0)),
            "llm_confidence": rca.get("confidence", 0.0),
            "human_tag": normalize_tag(human["taxonomy_tag"]),
            "human_step": int(human["root_error_step"]),
            "annotator": human.get("annotator", ann.get("annotator", "unknown")),
        })

    trial_name = trial_dir.name
    agent_trial_name = trial_dir.parent.name if nested_layout else trial_name
    agent_model = re.sub(r"_\d+steps$", "", agent_trial_name)

    metadata = {
        "trial": trial_name,
        "debugger_model": debugger_model or "unknown",
        "agent_model": agent_model,
        "annotation_policy": annotation_policy,
        "sample_size": len(pairs),
        "skipped_no_human": skipped,
    }

    return pairs, metadata


def compute_overall(pairs: list[dict]) -> dict:
    """Compute headline accuracy metrics."""
    n = len(pairs)
    if n == 0:
        return {
            "tag_exact_accuracy": 0.0,
            "tag_category_accuracy": 0.0,
            "step_exact_accuracy": 0.0,
            "step_within_1_accuracy": 0.0,
            "step_within_2_accuracy": 0.0,
            "step_within_3_accuracy": 0.0,
            "fully_correct": 0.0,
        }

    tag_exact = sum(1 for p in pairs if p["llm_tag"] == p["human_tag"])
    tag_cat = sum(1 for p in pairs if get_category(p["llm_tag"]) == get_category(p["human_tag"]))
    step_exact = sum(1 for p in pairs if p["llm_step"] == p["human_step"])
    step_w1 = sum(1 for p in pairs if abs(p["llm_step"] - p["human_step"]) <= 1)
    step_w2 = sum(1 for p in pairs if abs(p["llm_step"] - p["human_step"]) <= 2)
    step_w3 = sum(1 for p in pairs if abs(p["llm_step"] - p["human_step"]) <= 3)
    fully = sum(1 for p in pairs if p["llm_tag"] == p["human_tag"] and p["llm_step"] == p["human_step"])

    return {
        "tag_exact_accuracy": round(tag_exact / n, 4),
        "tag_category_accuracy": round(tag_cat / n, 4),
        "step_exact_accuracy": round(step_exact / n, 4),
        "step_within_1_accuracy": round(step_w1 / n, 4),
        "step_within_2_accuracy": round(step_w2 / n, 4),
        "step_within_3_accuracy": round(step_w3 / n, 4),
        "fully_correct": round(fully / n, 4),
    }


def compute_tag_metrics(pairs: list[dict]) -> dict:
    """Compute per-sub-type and per-category P/R/F1 plus confusion matrix."""
    if not pairs:
        return {"per_sub_type": {}, "per_category": {}, "confusion_matrix": {"labels": [], "matrix": []}}

    all_labels = sorted(set(p["human_tag"] for p in pairs) | set(p["llm_tag"] for p in pairs))
    label_idx = {label: i for i, label in enumerate(all_labels)}

    n_labels = len(all_labels)
    cm = [[0] * n_labels for _ in range(n_labels)]
    for p in pairs:
        h_idx = label_idx.get(p["human_tag"])
        l_idx = label_idx.get(p["llm_tag"])
        if h_idx is not None and l_idx is not None:
            cm[h_idx][l_idx] += 1

    per_sub_type = {}
    for label in all_labels:
        i = label_idx[label]
        tp = cm[i][i]
        fp = sum(cm[r][i] for r in range(n_labels)) - tp
        fn = sum(cm[i][c] for c in range(n_labels)) - tp
        support = sum(cm[i][c] for c in range(n_labels))
        prec = _safe_div(tp, tp + fp)
        rec = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * prec * rec, prec + rec)
        per_sub_type[label] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    all_cats = sorted(set(get_category(l) for l in all_labels))
    per_category = {}
    for cat in all_cats:
        tp = sum(1 for p in pairs if get_category(p["llm_tag"]) == cat and get_category(p["human_tag"]) == cat)
        fp = sum(1 for p in pairs if get_category(p["llm_tag"]) == cat and get_category(p["human_tag"]) != cat)
        fn = sum(1 for p in pairs if get_category(p["llm_tag"]) != cat and get_category(p["human_tag"]) == cat)
        support = sum(1 for p in pairs if get_category(p["human_tag"]) == cat)
        prec = _safe_div(tp, tp + fp)
        rec = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * prec * rec, prec + rec)
        per_category[cat] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    return {
        "per_sub_type": per_sub_type,
        "per_category": per_category,
        "confusion_matrix": {
            "labels": all_labels,
            "matrix": cm,
        },
    }


def compute_step_metrics(pairs: list[dict]) -> dict:
    """Compute root error step accuracy, MAE, bias."""
    if not pairs:
        return {
            "exact_match": 0.0, "within_1": 0.0, "within_2": 0.0, "within_3": 0.0,
            "mae": 0.0, "mean_signed_error": 0.0, "bias_direction": "neutral",
            "count_early": 0, "count_exact": 0, "count_late": 0,
        }

    n = len(pairs)
    diffs = [p["llm_step"] - p["human_step"] for p in pairs]
    abs_diffs = [abs(d) for d in diffs]

    exact = sum(1 for d in abs_diffs if d == 0)
    w1 = sum(1 for d in abs_diffs if d <= 1)
    w2 = sum(1 for d in abs_diffs if d <= 2)
    w3 = sum(1 for d in abs_diffs if d <= 3)
    mae = sum(abs_diffs) / n
    mse = sum(diffs) / n

    early = sum(1 for d in diffs if d < 0)
    late = sum(1 for d in diffs if d > 0)

    if mse < 0:
        bias = "early"
    elif mse > 0:
        bias = "late"
    else:
        bias = "neutral"

    return {
        "exact_match": round(exact / n, 4),
        "within_1": round(w1 / n, 4),
        "within_2": round(w2 / n, 4),
        "within_3": round(w3 / n, 4),
        "mae": round(mae, 4),
        "mean_signed_error": round(mse, 4),
        "bias_direction": bias,
        "count_early": early,
        "count_exact": exact,
        "count_late": late,
    }


def compute_per_annotator(pairs: list[dict]) -> dict:
    """Compute per-annotator accuracy breakdowns."""
    by_annotator: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_annotator[p["annotator"]].append(p)

    result = {}
    for name, ann_pairs in sorted(by_annotator.items()):
        n = len(ann_pairs)
        tag_exact = sum(1 for p in ann_pairs if p["llm_tag"] == p["human_tag"])
        tag_cat = sum(1 for p in ann_pairs if get_category(p["llm_tag"]) == get_category(p["human_tag"]))
        step_exact = sum(1 for p in ann_pairs if p["llm_step"] == p["human_step"])
        fully = sum(1 for p in ann_pairs if p["llm_tag"] == p["human_tag"] and p["llm_step"] == p["human_step"])
        result[name] = {
            "count": n,
            "tag_exact_accuracy": round(_safe_div(tag_exact, n), 4),
            "tag_category_accuracy": round(_safe_div(tag_cat, n), 4),
            "step_exact_accuracy": round(_safe_div(step_exact, n), 4),
            "fully_correct": round(_safe_div(fully, n), 4),
        }

    return result


def compute_accuracy(
    trial_dir: str | Path,
    annotation_policy: str = "final_or_single",
) -> dict[str, Any]:
    """Compute full accuracy report for a trial."""
    pairs, metadata = load_pairs(trial_dir, annotation_policy=annotation_policy)

    return {
        **metadata,
        "created_at": datetime.now().isoformat(),
        "overall": compute_overall(pairs),
        "taxonomy_tag": compute_tag_metrics(pairs),
        "root_error_step": compute_step_metrics(pairs),
        "per_annotator": compute_per_annotator(pairs),
    }


def quick_acc(trial_dir: str | Path, annotation_policy: str = "final_or_single") -> dict[str, Any]:
    """Return {n, tag_acc, cat_acc, step_acc} for a trial — one-liner for ablations.

    Use when comparing two trials side-by-side (e.g. --memory none vs --memory rag):
        quick_acc("results/debugger_results/trial_no_mem")
        quick_acc("results/debugger_results/trial_rag")
    """
    pairs, meta = load_pairs(trial_dir, annotation_policy=annotation_policy)
    ov = compute_overall(pairs)
    return {
        "trial": meta["trial"],
        "n": meta["sample_size"],
        "tag_acc": ov["tag_exact_accuracy"],
        "cat_acc": ov["tag_category_accuracy"],
        "step_acc": ov["step_exact_accuracy"],
        "fully_correct": ov["fully_correct"],
    }
