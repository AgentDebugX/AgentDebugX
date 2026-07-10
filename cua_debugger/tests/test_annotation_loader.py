import json
from pathlib import Path

import pytest

from debugger.memory.annotation_loader import load_annotation, load_annotations, Annotation


@pytest.fixture
def trial_dir(tmp_path: Path) -> Path:
    ann_dir = tmp_path / "annotations"
    ann_dir.mkdir()
    payload = {
        "task_id": "task-abc",
        "annotator": "tester",
        "llm_values": {
            "root_error_step": 12,
            "taxonomy_tag": "R10",
            "evidence": "llm evidence",
            "correction": "llm correction",
            "confidence": 0.85,
        },
        "human_values": {
            "root_error_step": 4,
            "taxonomy_tag": "P2",
            "evidence": "human evidence",
            "correction": "human correction",
            "confidence": "high",
        },
        "agrees_with_llm": False,
    }
    (ann_dir / "human_task-abc.json").write_text(json.dumps(payload))
    return tmp_path


@pytest.fixture
def trial_dir_v2(tmp_path: Path) -> Path:
    """Trial dir with dual-annotation list schema in annotations/."""
    ann_dir = tmp_path / "annotations"
    ann_dir.mkdir()
    payload = {
        "task_id": "task-multi",
        "trial": "test-trial",
        "llm_values": {
            "root_error_step": 10,
            "taxonomy_tag": "R5",
            "evidence": "llm evidence",
            "correction": "llm correction",
            "confidence": 0.9,
        },
        "human_values": [
            {
                "annotator": "Alice",
                "root_error_step": 3,
                "taxonomy_tag": "P2",
                "evidence": "Alice evidence",
                "correction": "Alice correction",
                "confidence": "high",
                "updated_at": "2026-03-15T10:00:00",
            },
            {
                "annotator": "Bob",
                "root_error_step": 5,
                "taxonomy_tag": "G1",
                "evidence": "Bob evidence",
                "correction": "Bob correction",
                "confidence": "medium",
                "updated_at": "2026-03-16T12:00:00",
            },
        ],
        "notes": "",
    }
    (ann_dir / "human_task-multi.json").write_text(json.dumps(payload))
    return tmp_path


# --- Original tests (singular shim) ---


def test_load_annotation_prefers_human(trial_dir: Path):
    ann = load_annotation(trial_dir, task_id="task-abc")
    assert isinstance(ann, Annotation)
    assert ann.source == "human"
    assert ann.root_error_step == 4
    assert ann.taxonomy_tag == "P2"
    assert ann.evidence == "human evidence"
    assert ann.correction == "human correction"


def test_load_annotation_falls_back_to_llm(tmp_path: Path):
    ann_dir = tmp_path / "annotations"
    ann_dir.mkdir()
    payload = {
        "task_id": "task-xyz",
        "llm_values": {
            "root_error_step": 7,
            "taxonomy_tag": "S2",
            "evidence": "llm evidence",
            "correction": "llm correction",
            "confidence": 0.6,
        },
        "human_values": None,
    }
    (ann_dir / "human_task-xyz.json").write_text(json.dumps(payload))

    ann = load_annotation(tmp_path, task_id="task-xyz")
    assert ann.source == "llm"
    assert ann.root_error_step == 7
    assert ann.taxonomy_tag == "S2"


def test_load_annotation_missing_returns_none(tmp_path: Path):
    assert load_annotation(tmp_path, task_id="nope") is None


# --- New tests (plural / v2 schema) ---


def test_load_annotations_v2_returns_list(trial_dir_v2: Path):
    result = load_annotations(trial_dir_v2, task_id="task-multi")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].annotator == "Alice"
    assert result[0].taxonomy_tag == "P2"
    assert result[0].source == "human"
    assert result[1].annotator == "Bob"
    assert result[1].taxonomy_tag == "G1"
    assert result[1].source == "human"


def test_load_annotations_v1_single_dict_compat(trial_dir: Path):
    # trial_dir has annotations/ with v1 single-dict human_values
    result = load_annotations(trial_dir, task_id="task-abc")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].source == "human"
    assert result[0].taxonomy_tag == "P2"
    assert result[0].annotator == "tester"


def test_load_annotations_missing_returns_empty_list(tmp_path: Path):
    result = load_annotations(tmp_path, task_id="nope")
    assert result == []


def test_load_annotation_shim_returns_first(trial_dir_v2: Path):
    ann = load_annotation(trial_dir_v2, task_id="task-multi")
    assert isinstance(ann, Annotation)
    assert ann.annotator == "Alice"
    assert ann.taxonomy_tag == "P2"


# --- final_decision tier (full_agreement consolidation) ---


def _write_annotation(ann_dir: Path, task_id: str, payload: dict) -> None:
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / f"human_{task_id}.json").write_text(json.dumps(payload))


def test_final_decision_overrides_human_values(tmp_path: Path):
    """Critical: when final_decision is present, it must beat per-annotator
    human_values — otherwise distill + metrics score against the wrong truth."""
    _write_annotation(tmp_path / "annotations", "task-final", {
        "task_id": "task-final",
        "human_values": [
            {"annotator": "Alice", "root_error_step": 3, "taxonomy_tag": "P2",
             "evidence": "alice ev", "correction": "alice corr", "confidence": "high"},
            {"annotator": "Bob",   "root_error_step": 5, "taxonomy_tag": "G1",
             "evidence": "bob ev",   "correction": "bob corr",   "confidence": "medium"},
        ],
        "llm_values": {
            "root_error_step": 10, "taxonomy_tag": "R5",
            "evidence": "llm ev", "correction": "llm corr", "confidence": 0.9,
        },
        "final_decision": {
            "annotator": "Tianyi",
            "root_error_step": 7,
            "taxonomy_tag": "S2",
            "evidence": "final ev",
            "correction": "final corr",
            "confidence": "high",
            "updated_at": "2026-05-01T18:28:38",
        },
    })

    results = load_annotations(tmp_path, task_id="task-final")
    assert len(results) == 1, "final_decision must collapse to one canonical entry"
    ann = results[0]
    assert ann.source == "human"
    assert ann.annotator == "Tianyi"
    assert ann.root_error_step == 7
    assert ann.taxonomy_tag == "S2"
    assert ann.evidence == "final ev"
    assert ann.correction == "final corr"


def test_final_decision_overrides_llm_values_when_no_human(tmp_path: Path):
    _write_annotation(tmp_path / "annotations", "task-final-llm", {
        "task_id": "task-final-llm",
        "human_values": None,
        "llm_values": {
            "root_error_step": 10, "taxonomy_tag": "R5",
            "evidence": "llm ev", "correction": "llm corr", "confidence": 0.9,
        },
        "final_decision": {
            "root_error_step": 4,
            "taxonomy_tag": "P1",
            "evidence": "final ev", "correction": "final corr",
            "confidence": "high",
        },
    })

    ann = load_annotation(tmp_path, task_id="task-final-llm")
    assert ann.source == "human"
    assert ann.root_error_step == 4
    assert ann.taxonomy_tag == "P1"


def test_missing_final_decision_falls_back_to_human(tmp_path: Path):
    """Regression guard: when final_decision is absent, behave as before."""
    _write_annotation(tmp_path / "annotations", "task-no-final", {
        "task_id": "task-no-final",
        "human_values": [{
            "annotator": "Alice", "root_error_step": 3, "taxonomy_tag": "P2",
            "evidence": "ev", "correction": "corr", "confidence": "high",
        }],
        "llm_values": {
            "root_error_step": 10, "taxonomy_tag": "R5",
            "evidence": "llm ev", "correction": "llm corr", "confidence": 0.9,
        },
    })

    results = load_annotations(tmp_path, task_id="task-no-final")
    assert len(results) == 1
    assert results[0].annotator == "Alice"
    assert results[0].root_error_step == 3
    assert results[0].taxonomy_tag == "P2"


def test_single_annotator_shape_returns_the_one_entry(tmp_path: Path):
    """``filters.single_annotator`` UUIDs have NO final_decision and exactly
    one entry in ``human_values`` (list).  The loader must return that one
    annotator's values so distill + metrics score against the right ground
    truth — anything else would silently regress the single-annotator half
    of the dataset."""
    _write_annotation(tmp_path / "annotations", "task-single", {
        "task_id": "task-single",
        "trial": "test",
        "human_values": [{
            "annotator": "Zeyi",
            "root_error_step": 11,
            "taxonomy_tag": "R8",
            "evidence": "z ev",
            "correction": "z corr",
            "confidence": "medium",
            "updated_at": "2026-04-10T09:00:00",
        }],
        "llm_values": {
            "root_error_step": 4, "taxonomy_tag": "P2",
            "evidence": "llm ev", "correction": "llm corr", "confidence": 0.7,
        },
        # NOTE: final_decision absent — matches real single_annotator files.
    })

    results = load_annotations(tmp_path, task_id="task-single")
    assert len(results) == 1
    assert results[0].source == "human"
    assert results[0].annotator == "Zeyi"
    assert results[0].root_error_step == 11
    assert results[0].taxonomy_tag == "R8"

    # Singular shim must agree.
    ann = load_annotation(tmp_path, task_id="task-single")
    assert ann.annotator == "Zeyi"
    assert ann.root_error_step == 11


def test_unpopulated_final_decision_is_ignored(tmp_path: Path):
    """A final_decision missing root_error_step or taxonomy_tag is treated as
    not-present and the fallback chain kicks in."""
    _write_annotation(tmp_path / "annotations", "task-empty-final", {
        "task_id": "task-empty-final",
        "human_values": [{
            "annotator": "Alice", "root_error_step": 3, "taxonomy_tag": "P2",
            "evidence": "ev", "correction": "corr", "confidence": "high",
        }],
        "final_decision": {
            "annotator": "Tianyi",
            "root_error_step": None,
            "taxonomy_tag": "",
            "confidence": "high",
        },
    })

    results = load_annotations(tmp_path, task_id="task-empty-final")
    assert len(results) == 1
    assert results[0].annotator == "Alice"
    assert results[0].taxonomy_tag == "P2"
