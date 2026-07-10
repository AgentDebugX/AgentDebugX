"""Smoke tests for Phase 9 multi-debugger loader + chosen_debugger save."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Repo root on sys.path so debugger.* imports resolve when pytest is run
# from the project root. Mirrors the pattern used by debugger_app.py:30-31.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _write_rca(trial_dir: Path, task_id: str, model: str) -> None:
    rca_dir = trial_dir / "rca"
    rca_dir.mkdir(parents=True, exist_ok=True)
    (rca_dir / f"rca_{task_id}.json").write_text(json.dumps({
        "task_id": task_id,
        "model": model,
        "root_error_step": 10,
        "taxonomy_tag": "R4",
        "evidence": f"evidence-from-{model}",
        "correction": f"correction-from-{model}",
        "confidence": 0.9,
    }), encoding="utf-8")


def test_load_debugger_refs_n3_ordered(tmp_path):
    from debugger.memory.annotation_loader import load_debugger_refs
    d1 = tmp_path / "trial_a"
    d2 = tmp_path / "trial_b"
    d3 = tmp_path / "trial_c"
    for d, m in [(d1, "claude-sonnet-4-5"), (d2, "gemini-3-flash"), (d3, "glm-4")]:
        _write_rca(d, "T1", m)
    refs = load_debugger_refs([d1, d2, d3], "T1")
    assert len(refs) == 3
    assert [r["model"] for r in refs] == ["claude-sonnet-4-5", "gemini-3-flash", "glm-4"]


def test_load_debugger_refs_n1_degrades(tmp_path):
    from debugger.memory.annotation_loader import load_debugger_refs
    d1 = tmp_path / "only"
    _write_rca(d1, "T1", "gemini-3-flash")
    refs = load_debugger_refs([d1], "T1")
    assert len(refs) == 1
    assert refs[0]["model"] == "gemini-3-flash"


def test_load_debugger_refs_skips_missing(tmp_path):
    from debugger.memory.annotation_loader import load_debugger_refs
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    d3 = tmp_path / "c"
    _write_rca(d1, "T1", "claude-sonnet-4-5")
    d2.mkdir()  # exists but no rca/ dir → still skipped
    _write_rca(d3, "T1", "glm-4")
    refs = load_debugger_refs([d1, d2, d3], "T1")
    assert [r["model"] for r in refs] == ["claude-sonnet-4-5", "glm-4"]


def test_load_debugger_refs_no_matches(tmp_path):
    from debugger.memory.annotation_loader import load_debugger_refs
    d1 = tmp_path / "a"
    _write_rca(d1, "T1", "claude-sonnet-4-5")
    assert load_debugger_refs([d1], "TX-not-present") == []


def _make_task_dict() -> dict:
    return {
        "task_id": "TT",
        "root_error_step": 5,
        "taxonomy_tag": "G1",
        "evidence": "llm-evidence",
        "correction": "llm-correction",
        "confidence": 0.85,
    }


def _make_human_values() -> dict:
    return {
        "root_error_step": 5,
        "taxonomy_tag": "G1",
        "evidence": "h-evidence",
        "correction": "h-correction",
        "confidence": "high",
    }


def test_save_annotation_writes_chosen_debugger(tmp_path):
    from debugger.vis.debugger_app import save_annotation
    path = save_annotation(
        str(tmp_path), "TT", _make_task_dict(),
        _make_human_values(), "Yinting", "",
        chosen_debugger="gemini-3-flash",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["human_values"][0]["chosen_debugger"] == "gemini-3-flash"


def test_save_annotation_omits_when_none(tmp_path):
    from debugger.vis.debugger_app import save_annotation
    path = save_annotation(
        str(tmp_path), "TT", _make_task_dict(),
        _make_human_values(), "Yinting", "",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "chosen_debugger" not in data["human_values"][0]


def test_save_annotation_preserves_chosen_debugger_on_update(tmp_path):
    """BLOCK 3 from iteration-1 review: pin the cross-plan contract that
    re-saving an existing annotation does NOT silently strip chosen_debugger,
    as long as the caller (Plan 09-02 picker) rehydrates the value from the
    saved entry and threads it back through. This test simulates that flow.
    """
    from debugger.vis.debugger_app import save_annotation
    # First save: annotator picks Debugger A.
    path1 = save_annotation(
        str(tmp_path), "TT", _make_task_dict(),
        _make_human_values(), "Yinting", "",
        chosen_debugger="gemini-3-flash",
    )
    data1 = json.loads(path1.read_text(encoding="utf-8"))
    assert data1["human_values"][0]["chosen_debugger"] == "gemini-3-flash"

    # Second save: same annotator returns to edit. Plan 09-02 rehydrates
    # _chosen_debugger_model from data1["human_values"][0]["chosen_debugger"]
    # on task switch, so the save call passes the SAME value back.
    rehydrated = data1["human_values"][0]["chosen_debugger"]
    # Tweak a form field to simulate a real edit:
    edited = _make_human_values()
    edited["evidence"] = "edited-evidence"
    path2 = save_annotation(
        str(tmp_path), "TT", _make_task_dict(),
        edited, "Yinting", "",
        chosen_debugger=rehydrated,
    )
    data2 = json.loads(path2.read_text(encoding="utf-8"))
    # Single annotator entry, updated in place (per Phase 1 append-by-annotator semantics):
    assert len(data2["human_values"]) == 1
    assert data2["human_values"][0]["annotator"] == "Yinting"
    assert data2["human_values"][0]["evidence"] == "edited-evidence"
    # Critical assertion: chosen_debugger preserved across update.
    assert data2["human_values"][0]["chosen_debugger"] == "gemini-3-flash"
