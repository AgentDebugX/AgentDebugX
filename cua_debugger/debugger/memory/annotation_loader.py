"""
Step 0 — annotation loader.

Loads per-task annotation files from <trial_dir>/annotations/human_<task_id>.json.

Schema:
  * ``final_decision`` (optional) — single consolidated record produced by
    multi-annotator review.  Present only for UUIDs in
    ``annotation_agreement.json::filters/full_agreement``.  When present, this
    is the canonical ground truth and supersedes both ``human_values`` and
    ``llm_values`` for distill + metrics.
  * ``human_values`` — list of per-annotator dicts (legacy single-dict form
    is auto-promoted to a one-element list).
  * ``llm_values``   — LLM-only fallback when no human signal is available.

load_annotations() (plural) returns list[Annotation].
load_annotation()  (singular) is a legacy shim returning Optional[Annotation].

Selection priority: ``final_decision`` > ``human_values`` > ``llm_values``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional
from pydantic import BaseModel


class Annotation(BaseModel):
    task_id: str
    source: Literal["human", "llm"]
    root_error_step: int
    taxonomy_tag: str
    evidence: str
    correction: str
    confidence: Any  # human uses "high"/"medium"/"low"; llm uses float
    annotator: str = ""

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")

    def to_json(self) -> dict:
        return self.model_dump(mode="json")


def _is_populated(values: Optional[dict]) -> bool:
    if not values:
        return False
    return values.get("root_error_step") is not None and bool(values.get("taxonomy_tag"))


def load_annotations(trial_dir: Path, task_id: str) -> list[Annotation]:
    """Return all annotations for *task_id* as a list.

    Reads ``annotations/human_<task_id>.json``.  Handles both the dual-annotation
    list schema (human_values is a list) and the legacy single-dict schema
    (human_values is a dict — auto-promoted to a one-element list).
    Returns ``[]`` when no file exists.
    """
    path = Path(trial_dir) / "annotations" / f"human_{task_id}.json"
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))

    # Tier 1: ``final_decision`` is the consolidated multi-annotator ground
    # truth (present only for full_agreement UUIDs). When populated it
    # supersedes both human_values and llm_values for both distill (which
    # consumes annotation_dict) and metrics (which consumes the Annotation
    # object directly), so they always score against the canonical record.
    final = payload.get("final_decision")
    if _is_populated(final):
        return [Annotation(
            task_id=payload.get("task_id", task_id),
            source="human",
            root_error_step=int(final["root_error_step"]),
            taxonomy_tag=str(final["taxonomy_tag"]),
            evidence=str(final.get("evidence", "")),
            correction=str(final.get("correction", "")),
            confidence=final.get("confidence"),
            annotator=final.get("annotator", payload.get("annotator", "")),
        )]

    human = payload.get("human_values")
    llm = payload.get("llm_values")

    # Normalise human_values to a list
    if isinstance(human, dict):
        human_list = [human] if human else []
    elif isinstance(human, list):
        human_list = human
    else:
        human_list = []

    results: list[Annotation] = []
    for entry in human_list:
        if _is_populated(entry):
            results.append(Annotation(
                task_id=payload.get("task_id", task_id),
                source="human",
                root_error_step=int(entry["root_error_step"]),
                taxonomy_tag=str(entry["taxonomy_tag"]),
                evidence=str(entry.get("evidence", "")),
                correction=str(entry.get("correction", "")),
                confidence=entry.get("confidence"),
                annotator=entry.get("annotator", payload.get("annotator", "")),
            ))

    if not results and _is_populated(llm):
        results.append(Annotation(
            task_id=payload.get("task_id", task_id),
            source="llm",
            root_error_step=int(llm["root_error_step"]),
            taxonomy_tag=str(llm["taxonomy_tag"]),
            evidence=str(llm.get("evidence", "")),
            correction=str(llm.get("correction", "")),
            confidence=llm.get("confidence"),
        ))

    return results


def load_annotation(trial_dir: Path, task_id: str) -> Optional[Annotation]:
    """Legacy shim — returns the first annotation or None."""
    annotations = load_annotations(trial_dir, task_id)
    return annotations[0] if annotations else None


def load_debugger_refs(trial_dirs: list[str | Path], task_id: str) -> list[dict]:
    """Load RCA references for one task across debugger trial dirs."""
    refs: list[dict] = []
    for trial_dir in trial_dirs:
        path = Path(trial_dir) / "rca" / f"rca_{task_id}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        refs.append({
            "model": payload.get("model") or Path(trial_dir).name,
            "root_error_step": payload.get("root_error_step"),
            "taxonomy_tag": payload.get("taxonomy_tag"),
            "evidence": payload.get("evidence", ""),
            "correction": payload.get("correction", ""),
        })
    return refs
