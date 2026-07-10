"""Unit tests for debugger.evolving.{protocol,state}. Phase 12 Plan 12-01."""
import pytest
from pydantic import ValidationError

from debugger.evolving import EvolvingOp, TaxonomyState, AuditEntry


def test_initial_state_is_empty():
    s = TaxonomyState()
    assert s.size() == 0
    assert s.categories == {}
    assert s.subtypes == {}


def test_reuse_unknown_subtype_raises():
    s = TaxonomyState()
    with pytest.raises(ValueError, match="REUSE references unknown subtype"):
        s.apply_op(EvolvingOp.REUSE, {"subtype_code": "X1"})


def test_discover_append_adds_subtype():
    s = TaxonomyState()
    s2 = s.apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Perception-ish",
        "new_subtype_code": "X1",
        "name": "Visual Hallucination",
        "definition": "Sees things that aren't there.",
    })
    assert s.size() == 0  # original unchanged
    assert s2.size() == 1
    assert "X1" in s2.subtypes
    assert s2.subtypes["X1"]["parent"] == "Perception-ish"


def test_discover_append_duplicate_raises():
    s = TaxonomyState().apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X1",
        "name": "n", "definition": "d",
    })
    with pytest.raises(ValueError, match="DISCOVER_APPEND duplicate"):
        s.apply_op(EvolvingOp.DISCOVER_APPEND, {
            "parent_category": "Cat", "new_subtype_code": "X1",
            "name": "n", "definition": "d2",
        })


def test_edit_rename_updates_definition():
    s = TaxonomyState().apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X1",
        "name": "n", "definition": "original",
    })
    s2 = s.apply_op(EvolvingOp.EDIT_RENAME, {
        "subtype_code": "X1", "new_definition": "refined",
    })
    assert s2.subtypes["X1"]["definition"] == "refined"
    assert "X1" in s2.subtypes  # code unchanged


def test_edit_split_one_into_two():
    s = TaxonomyState().apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X1",
        "name": "n", "definition": "d",
    })
    s2 = s.apply_op(EvolvingOp.EDIT_SPLIT, {
        "subtype_code": "X1",
        "new_subtypes": [
            {"new_code": "X1a", "name": "na", "definition": "da"},
            {"new_code": "X1b", "name": "nb", "definition": "db"},
        ],
    })
    assert "X1" not in s2.subtypes
    assert "X1a" in s2.subtypes and "X1b" in s2.subtypes
    assert s2.subtypes["X1a"]["parent"] == "Cat"


def test_edit_merge_two_into_one():
    s = TaxonomyState()
    s = s.apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X1",
        "name": "n1", "definition": "d1",
    })
    s = s.apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X2",
        "name": "n2", "definition": "d2",
    })
    s2 = s.apply_op(EvolvingOp.EDIT_MERGE, {
        "source_codes": ["X1", "X2"],
        "new_code": "X12",
        "name": "merged",
        "definition": "merged def",
    })
    assert "X1" not in s2.subtypes and "X2" not in s2.subtypes
    assert s2.subtypes["X12"]["merged_from"] == ["X1", "X2"]


def test_state_json_roundtrip():
    s = TaxonomyState().apply_op(EvolvingOp.DISCOVER_APPEND, {
        "parent_category": "Cat", "new_subtype_code": "X1",
        "name": "n", "definition": "d",
    })
    s2 = TaxonomyState.from_json(s.to_json())
    assert s2.subtypes == s.subtypes
    assert s2.categories == s.categories


def test_audit_entry_shape():
    # All 6 required fields present → OK
    e = AuditEntry(
        op="REUSE", case_id="t1", timestamp="2026-05-22T00:00:00",
        taxonomy_state_before={}, taxonomy_state_after={}, reasoning="ok",
    )
    assert e.op == "REUSE"
    # Missing case_id → ValidationError
    with pytest.raises(ValidationError):
        AuditEntry(
            op="REUSE", timestamp="t",
            taxonomy_state_before={}, taxonomy_state_after={}, reasoning="r",
        )
