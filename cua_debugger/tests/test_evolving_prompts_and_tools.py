"""Plan 12-01 Task 2: empty-start prompt and tool-schema invariants."""
import pytest

from debugger.evolving import EvolvingOp
from debugger.evolving.prompts import EVOLVING_RCA_SYSTEM_PROMPT
from debugger.evolving.tools import get_evolving_tools, EVOLVING_FINISH_TOOL


FORBIDDEN_SEED_LABELS = [
    "P1", "P2", "P3", "P4", "P5",
    "G1", "G2", "G3", "G4",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7",
    "R8", "R9", "R10", "R11", "R12", "R13",
    "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    "IF1", "IF2",
    "Perception", "Grounding & Interaction",
    "Task Reasoning & Control",
    "External / System", "Infeasible Task",
]


def test_prompt_has_no_seed_labels():
    for lbl in FORBIDDEN_SEED_LABELS:
        assert lbl not in EVOLVING_RCA_SYSTEM_PROMPT, (
            f"Seed label {lbl!r} leaked into EVOLVING_RCA_SYSTEM_PROMPT — "
            f"Phase 12 locked decision: empty start, no priors."
        )


def test_prompt_mentions_all_five_ops():
    for op in EvolvingOp:
        assert op.value in EVOLVING_RCA_SYSTEM_PROMPT, (
            f"Op {op.value} not mentioned in EVOLVING_RCA_SYSTEM_PROMPT"
        )


def test_get_evolving_tools_returns_two_tools():
    tools = get_evolving_tools()
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"get_step_details", "finish"}


def test_finish_tool_taxonomy_tag_is_free_form_string():
    props = EVOLVING_FINISH_TOOL["input_schema"]["properties"]
    assert props["taxonomy_tag"]["type"] == "string"
    assert "enum" not in props["taxonomy_tag"]


def test_finish_tool_taxonomy_op_enum_has_five_values():
    op_type = (
        EVOLVING_FINISH_TOOL["input_schema"]["properties"]
        ["taxonomy_op"]["properties"]["type"]
    )
    assert set(op_type["enum"]) == {op.value for op in EvolvingOp}
    assert len(op_type["enum"]) == 5


def test_tools_are_anthropic_api_clean():
    for t in get_evolving_tools():
        assert "_contexts" not in t, (
            f"Tool {t['name']!r} carries _contexts — would crash Anthropic API"
        )
