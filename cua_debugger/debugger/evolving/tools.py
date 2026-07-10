"""Anthropic-compatible tool schemas for the evolving-taxonomy RCA loop (Phase 12).

Reuses GET_STEP_DETAILS_TOOL from debugger/tools.py verbatim (minus the _contexts
marker). The finish tool is new: taxonomy_tag is free-form (not bound to
ALL_SUBTYPES) and a taxonomy_op field is added.
"""
from debugger.tools import GET_STEP_DETAILS_TOOL
from debugger.evolving.protocol import EvolvingOp


def _strip_contexts(tool: dict) -> dict:
    return {k: v for k, v in tool.items() if k != "_contexts"}


EVOLVING_FINISH_TOOL = {
    "name": "finish",
    "description": (
        "Submit the final RCA result for this case. Include the root error "
        "step, a free-form taxonomy_tag (a subtype code you are reusing or "
        "creating), evidence, correction, confidence, per_step_summaries, and "
        "the taxonomy_op describing how the taxonomy state should evolve."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "root_error_step": {"type": "integer"},
            "taxonomy_tag": {
                "type": "string",
                "description": (
                    "The subtype code assigned to this case. Free-form — must "
                    "either match an existing subtype in taxonomy_state_so_far "
                    "(for REUSE) or match the new_subtype_code you just declared "
                    "in taxonomy_op (for DISCOVER_APPEND / EDIT_SPLIT result / "
                    "EDIT_MERGE result)."
                ),
            },
            "evidence": {"type": "string"},
            "correction": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "per_step_summaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_num": {"type": "integer"},
                        "intent_summary": {"type": "string"},
                        "outcome_summary": {"type": "string"},
                        "summary_source": {
                            "type": "string",
                            "enum": ["debugger_inspected"],
                        },
                    },
                    "required": [
                        "step_num", "intent_summary",
                        "outcome_summary", "summary_source",
                    ],
                },
            },
            "taxonomy_op": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [op.value for op in EvolvingOp],
                    },
                    "op_args": {
                        "type": "object",
                        "description": (
                            "Op-specific arguments. REUSE: {subtype_code}. "
                            "DISCOVER_APPEND: {parent_category, new_subtype_code, "
                            "name, definition}. EDIT_RENAME: {subtype_code, "
                            "new_definition}. EDIT_SPLIT: {subtype_code, "
                            "new_subtypes: [{new_code, name, definition}, ...]}. "
                            "EDIT_MERGE: {source_codes: [..], new_code, name, "
                            "definition}."
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Why this op (and not another). Logged to the audit "
                            "trail."
                        ),
                    },
                },
                "required": ["type", "op_args", "reasoning"],
            },
        },
        "required": [
            "root_error_step",
            "taxonomy_tag",
            "evidence",
            "correction",
            "confidence",
            "per_step_summaries",
            "taxonomy_op",
        ],
    },
}


def get_evolving_tools() -> list[dict]:
    """Return tools ready to pass to client.messages.create(tools=...)."""
    return [_strip_contexts(GET_STEP_DETAILS_TOOL), EVOLVING_FINISH_TOOL]
