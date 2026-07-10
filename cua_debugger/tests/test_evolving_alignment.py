"""Plan 12-03 Task 1: projection and coverage/purity/NMI on stub data."""
import json
from pathlib import Path

import pytest

from debugger.evolving.alignment import (
    project_predictions,
    compute_coverage_purity_nmi,
    load_run_artifacts,
)
from debugger.evolving import TaxonomyState


def _write_human_ann(dir_: Path, task_id: str, tag: str, step: int):
    (dir_ / f"human_{task_id}.json").write_text(json.dumps({
        "task_id": task_id,
        "human_values": [{
            "taxonomy_tag": tag, "root_error_step": step,
            "evidence": "e", "correction": "c", "confidence": "high",
            "annotator": "alice",
        }],
    }), encoding="utf-8")


def test_project_predictions_drops_NONE(tmp_path):
    _write_human_ann(tmp_path, "t1", "G1", 3)
    _write_human_ann(tmp_path, "t2", "R10", 5)
    per_case = [
        {"task_id": "t1", "taxonomy_tag": "X1", "root_error_step": 3, "confidence": 0.9},
        {"task_id": "t2", "taxonomy_tag": "X2", "root_error_step": 5, "confidence": 0.8},
    ]
    mapping = {"X1": "G1", "X2": "NONE"}
    pairs = project_predictions(per_case, mapping, tmp_path)
    assert len(pairs) == 1
    assert pairs[0]["llm_tag"] == "G1"
    assert pairs[0]["human_tag"] == "G1"


def test_project_predictions_drops_missing_annotation(tmp_path):
    # Only write t1's annotation
    _write_human_ann(tmp_path, "t1", "G1", 3)
    per_case = [
        {"task_id": "t1", "taxonomy_tag": "X1", "root_error_step": 3, "confidence": 0.9},
        {"task_id": "t2", "taxonomy_tag": "X1", "root_error_step": 5, "confidence": 0.7},
    ]
    mapping = {"X1": "G1"}
    pairs = project_predictions(per_case, mapping, tmp_path)
    assert len(pairs) == 1


def test_coverage_with_full_mapping(tmp_path):
    _write_human_ann(tmp_path, "t1", "G1", 3)
    _write_human_ann(tmp_path, "t2", "R10", 5)
    per_case = [
        {"task_id": "t1", "taxonomy_tag": "X1", "root_error_step": 3, "confidence": 0.9},
        {"task_id": "t2", "taxonomy_tag": "X2", "root_error_step": 5, "confidence": 0.8},
    ]
    mapping = {"X1": "G1", "X2": "R10"}
    m = compute_coverage_purity_nmi(mapping, per_case, tmp_path)
    assert m["coverage"] == 1.0
    assert m["purity"] == 1.0


def test_purity_no_NONE():
    mapping = {"X1": "G1", "X2": "R10", "X3": "S4"}
    m = compute_coverage_purity_nmi(mapping, [], Path("."))
    assert m["purity"] == 1.0


def test_purity_all_NONE():
    mapping = {"X1": "NONE", "X2": "NONE"}
    m = compute_coverage_purity_nmi(mapping, [], Path("."))
    assert m["purity"] == 0.0


def test_nmi_in_unit_interval(tmp_path):
    # Construct a perfect-match scenario so NMI > 0
    _write_human_ann(tmp_path, "t1", "G1", 1)
    _write_human_ann(tmp_path, "t2", "G1", 2)
    _write_human_ann(tmp_path, "t3", "R10", 3)
    per_case = [
        {"task_id": "t1", "taxonomy_tag": "X1", "root_error_step": 1, "confidence": 0.9},
        {"task_id": "t2", "taxonomy_tag": "X1", "root_error_step": 2, "confidence": 0.9},
        {"task_id": "t3", "taxonomy_tag": "X2", "root_error_step": 3, "confidence": 0.9},
    ]
    mapping = {"X1": "G1", "X2": "R10"}
    m = compute_coverage_purity_nmi(mapping, per_case, tmp_path)
    assert 0.0 <= m["nmi"] <= 1.0


def test_load_run_artifacts_examples_by_code_capped_at_3(tmp_path):
    run_dir = tmp_path / "run"
    per_case_dir = run_dir / "per_case_rca"
    per_case_dir.mkdir(parents=True)
    # 5 cases all assigned to "X1"
    for i in range(5):
        (per_case_dir / f"rca_t{i}.json").write_text(json.dumps({
            "task_id": f"t{i}", "taxonomy_tag": "X1",
            "root_error_step": i, "confidence": 0.5 + i * 0.1,
            "evidence": "e", "correction": "c",
        }), encoding="utf-8")
    # Minimal final taxonomy
    (run_dir / "final_taxonomy.json").write_text(json.dumps(
        TaxonomyState().apply_op("DISCOVER_APPEND", {
            "parent_category": "Cat", "new_subtype_code": "X1",
            "name": "n", "definition": "d",
        }).to_json()
    ), encoding="utf-8")
    arts = load_run_artifacts(run_dir)
    assert len(arts["examples_by_code"]["X1"]) == 3
    # Highest-confidence cases first
    confidences = [e["confidence"] for e in arts["examples_by_code"]["X1"]]
    assert confidences == sorted(confidences, reverse=True)
