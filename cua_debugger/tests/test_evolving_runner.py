"""Unit tests for debugger.evolving.runner helpers (no real API calls)."""
from unittest.mock import patch, MagicMock
from pathlib import Path

from debugger.evolving.runner import (
    format_taxonomy_state,
    run_evolving_rca_on_case,
)
from debugger.evolving.state import TaxonomyState
from debugger.evolving.protocol import EvolvingOp


def test_format_taxonomy_state_empty():
    s = TaxonomyState()
    out = format_taxonomy_state(s)
    assert "empty" in out.lower()
    # Must NOT mention any seed taxonomy labels (empty-start invariant).
    for forbidden in ("P1", "G1", "R1", "S1", "IF1", "IF2", "Perception",
                      "Grounding", "Reasoning", "External", "Infeasible"):
        assert forbidden not in out, f"seed label leaked: {forbidden}"


def test_format_taxonomy_state_populated():
    s = TaxonomyState()
    s = s.apply_op(EvolvingOp.DISCOVER_APPEND.value, {
        "parent_category": "Custom", "new_subtype_code": "X1",
        "name": "test-subtype", "definition": "test definition",
        "reasoning": "unit test",
    })
    out = format_taxonomy_state(s)
    assert "X1" in out
    assert "test-subtype" in out
    assert "empty" not in out.lower()


def test_run_evolving_rca_on_case_mocked(tmp_path: Path):
    """run_evolving_rca_on_case advances state and emits a valid AuditEntry
    when run_react_loop returns a well-formed finish payload."""
    # Stub trajectory directory layout that ingest() will accept.
    # The simplest path: monkey-patch ingest() rather than build a real trajectory.
    fake_ir = MagicMock()
    fake_ir.task_id = "fake_task_001"
    fake_ir.terminal_step = 5
    fake_ir.failure_summary = "stub"
    fake_ir.trajectory = []
    fake_ir.fmt = "claude"
    fake_ir.instruction = "test instruction"
    fake_ir.status = "failure"
    fake_ir.error_msg = "test error"
    fake_ir.is_infeasible = False

    fake_finish = {
        "root_error_step": 3,
        "taxonomy_tag": "X1",
        "evidence": "fake evidence",
        "correction": "fake correction",
        "confidence": 0.8,
        "per_step_summaries": [],
        "taxonomy_op": {
            "type": EvolvingOp.DISCOVER_APPEND.value,
            "op_args": {
                "parent_category": "Cat1",
                "new_subtype_code": "X1",
                "name": "fake",
                "definition": "fake def",
                "reasoning": "test",
            },
            "reasoning": "first discovery",
        },
    }
    with patch("debugger.evolving.runner.IngestionResult.from_directory", return_value=fake_ir), \
         patch("debugger.evolving.runner.run_react_loop",
               return_value=(fake_finish, ["thinking"], None)):
        rca_dict, audit, new_state = run_evolving_rca_on_case(
            traj_dir=tmp_path,
            current_state=TaxonomyState(),
            client=MagicMock(),
            model="test-model",
            osworld_root=tmp_path,
            verbose=False,
        )

    assert rca_dict["task_id"] == "fake_task_001"
    assert rca_dict["taxonomy_tag"] == "X1"
    assert audit.op == EvolvingOp.DISCOVER_APPEND.value
    assert audit.case_id == "fake_task_001"
    assert new_state.size() == 1
