"""
Anthropic tool descriptors for the trajectory debugger agents.

This package replaces the old ``debugger/tools.py`` module.  The migration
preserves every previously-exported symbol so existing imports
(``from debugger.tools import TOOLS / RCA_TOOLS / DISCUSSION_TOOLS / get_tools``)
continue to work unchanged.

Each tool descriptor carries an internal ``_contexts`` set that declares which
agent contexts (``"general"`` / ``"rca"`` / ``"discussion"``) may use it.  The
``get_tools(context)`` helper strips that marker before returning the list, so
the result is safe to pass straight to ``client.messages.create(tools=...)``.

Three new descriptors are appended below with ``_contexts={"rca"}`` so they
flow into ``RCA_TOOLS`` automatically; tool *implementations* live in the
sibling module ``debugger/tools/lesson_explorer.py``.
"""

from debugger.taxonomy import TAXONOMY_CATEGORIES, ALL_SUBTYPES

# Re-export so callers can keep doing ``from debugger.tools.lesson_explorer import …``
# without poking at the package layout.
from . import lesson_explorer  # noqa: F401


# ---------------------------------------------------------------------------
# Context marker helper
# ---------------------------------------------------------------------------

_ALL_CONTEXTS = {"general", "rca", "rca_with_lessons", "discussion"}


def get_tools(context: str) -> list[dict]:
    """Return API-ready tool dicts for the given agent context.

    Strips the internal ``_contexts`` marker so the result can be passed
    directly to ``client.messages.create(tools=...)``.

    Raises ``ValueError`` when *context* is not a recognised context name.
    """
    if context not in _ALL_CONTEXTS:
        raise ValueError(
            f"Unknown tool context {context!r}. "
            f"Valid contexts: {sorted(_ALL_CONTEXTS)}"
        )
    out: list[dict] = []
    for tool in _TOOL_REGISTRY:
        if context in tool["_contexts"]:
            clean = {k: v for k, v in tool.items() if k != "_contexts"}
            out.append(clean)
    return out


# ---------------------------------------------------------------------------
# Original tool descriptors (verbatim from the old module)
# ---------------------------------------------------------------------------

LOAD_TRAJECTORY_TOOL = {
    "_contexts": {"general", "discussion"},  # rca: trajectory + step index injected into initial prompt
    "name": "load_trajectory",
    "description": (
        "Load a trajectory directory. Returns the task instruction, result "
        "score, and a step index showing step_num, action_type, whether the "
        "step has an execution error, and whether a screenshot is available. "
        "Call this first before inspecting individual steps."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path or OSWorld-relative path to the trajectory "
                    "directory."
                ),
            }
        },
        "required": ["path"],
    },
}

GET_STEP_DETAILS_TOOL = {
    "_contexts": {"general", "rca", "rca_with_lessons", "discussion"},
    "name": "get_step_details",
    "description": (
        "Get full details for a specific step: action code, execution error, "
        "agent reasoning, reward, done status, plus two screenshots — "
        "the input screenshot (screen state the agent saw BEFORE choosing "
        "this action, i.e. the previous step's result) and the result "
        "screenshot (screen state AFTER the action executed). For step 1, "
        "the input screenshot may not be available if no initial state was saved. "
        "In RCA mode, use these textual and visual details to produce that "
        "step's intent_summary and outcome_summary in finish()."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "step_num": {
                "type": "integer",
                "description": "The step number to inspect.",
            }
        },
        "required": ["step_num"],
    },
}


GENERAL_FINISH_TOOL = {
    "_contexts": {"general"},
    "name": "finish",
    "description": (
        "Submit the final analysis report. Call this once you have inspected "
        "all relevant steps. The input to this tool IS the report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_summary": {
                "type": "string",
                "description": "2-4 sentence narrative of what happened.",
            },
            "primary_error_category": {
                "type": "string",
                "enum": ALL_SUBTYPES + TAXONOMY_CATEGORIES + ["None"],
                "description": (
                    "The single most impactful error sub-type code "
                    "(e.g. P1, G2, R10, S4) or category, or 'None' if "
                    "the task succeeded."
                ),
            },
            "error_taxonomy": {
                "type": "object",
                "description": "One key per taxonomy category.",
                "properties": {
                    cat: {
                        "type": "object",
                        "properties": {
                            "present": {"type": "boolean"},
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "steps": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["present", "evidence", "steps"],
                    }
                    for cat in TAXONOMY_CATEGORIES
                },
            },
            "per_step_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_num": {"type": "integer"},
                        "error_categories": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "analysis": {
                            "type": "string",
                            "description": "One-sentence summary.",
                        },
                        "screen_state": {
                            "type": "string",
                            "description": (
                                "Key UI elements visible in the screenshot. "
                                "Empty string if step is clean."
                            ),
                        },
                        "what_went_wrong": {
                            "type": "string",
                            "description": (
                                "Specific description of the error. "
                                "Empty string if none."
                            ),
                        },
                        "correct_approach": {
                            "type": "string",
                            "description": (
                                "What the agent should have done instead. "
                                "Empty string if correct."
                            ),
                        },
                        "root_cause": {
                            "type": "string",
                            "description": (
                                "Underlying reason for the error. "
                                "Empty string if none."
                            ),
                        },
                    },
                    "required": [
                        "step_num", "error_categories", "analysis",
                        "screen_state", "what_went_wrong",
                        "correct_approach", "root_cause",
                    ],
                },
            },
            "recommendations": {
                "type": "string",
                "description": "Concrete numbered suggestions to fix the agent.",
            },
        },
        "required": [
            "overall_summary",
            "primary_error_category",
            "error_taxonomy",
            "per_step_analysis",
            "recommendations",
        ],
    },
}

RCA_FINISH_TOOL = {
    "_contexts": {"rca", "rca_with_lessons"},
    "name": "finish",
    "description": (
        "Submit the final RCA result. Call this once you have identified "
        "the root error step. You MUST include a confidence score."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "root_error_step": {
                "type": "integer",
                "description": (
                    "Step number of the earliest root cause (N <= F where F "
                    "is the Terminal Failure Step — the last step of the "
                    "failed trajectory). This is the step whose mistake "
                    "propagated to all later failures."
                ),
            },
            "taxonomy_tag": {
                "type": "string",
                "enum": ALL_SUBTYPES + TAXONOMY_CATEGORIES,
                "description": (
                    "The error sub-type code (e.g. P1, G2, R10, S4) or "
                    "category name. Prefer specific sub-type codes."
                ),
            },
            "evidence": {
                "type": "string",
                "description": (
                    "Specific, grounded evidence from the trajectory (action code, "
                    "error output, or screenshot observation) that identifies this "
                    "step as the root cause."
                ),
            },
            "correction": {
                "type": "string",
                "description": (
                    "Concrete description of what the agent should have done "
                    "differently at root_error_step to prevent the cascade."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Your confidence that this is truly the root error step, "
                    "as a float between 0.0 (no confidence) and 1.0 (certain). "
                    "Consider: how clearly does the evidence support causality? "
                    "Are there ambiguous alternative root causes?"
                ),
            },
            "per_step_summaries": {
                "type": "array",
                "description": (
                    "Summaries for each step you inspected with "
                    "get_step_details. Include only steps whose screenshots "
                    "or details you actually inspected."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "step_num": {
                            "type": "integer",
                            "description": "The inspected step number.",
                        },
                        "intent_summary": {
                            "type": "string",
                            "description": (
                                "One concise sentence describing what the "
                                "agent appeared to be trying to do at this step, "
                                "inferred from textual trajectory fields such as "
                                "action code, reasoning, and tool use."
                            ),
                        },
                        "outcome_summary": {
                            "type": "string",
                            "description": (
                                "One concise sentence describing the observable "
                                "screen/result change after this action, inferred "
                                "from the input/result screenshots plus execution "
                                "metadata."
                            ),
                        },
                        "summary_source": {
                            "type": "string",
                            "enum": ["debugger_inspected"],
                            "description": (
                                "Use debugger_inspected because this summary "
                                "comes from get_step_details inspection."
                            ),
                        },
                    },
                    "required": [
                        "step_num",
                        "intent_summary",
                        "outcome_summary",
                        "summary_source",
                    ],
                },
            },
        },
        "required": [
            "root_error_step",
            "taxonomy_tag",
            "evidence",
            "correction",
            "confidence",
            "per_step_summaries",
        ],
    },
}


PROPOSE_ANNOTATION_TOOL = {
    "_contexts": {"discussion"},
    "name": "propose_annotation",
    "description": (
        "Propose corrected annotation values after discussion with the human "
        "annotator. Call this when you and the human have agreed on corrections "
        "to the original RCA. Include ALL fields even if unchanged."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "root_error_step": {
                "type": "integer",
                "description": "The root error step number.",
            },
            "taxonomy_tag": {
                "type": "string",
                "enum": ALL_SUBTYPES + TAXONOMY_CATEGORIES,
                "description": "Error sub-type code (e.g. P1, G2, R10).",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "mid", "high"],
                "description": "Annotation confidence level.",
            },
            "evidence": {
                "type": "string",
                "description": "Specific evidence from the trajectory.",
            },
            "correction": {
                "type": "string",
                "description": "What the agent should have done differently.",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Brief explanation of what changed from the original RCA "
                    "and why, based on the discussion."
                ),
            },
        },
        "required": [
            "root_error_step", "taxonomy_tag", "confidence",
            "evidence", "correction", "reasoning",
        ],
    },
}


# ---------------------------------------------------------------------------
# NEW — RCA-context lesson exploration descriptors
# ---------------------------------------------------------------------------

LOOKUP_LESSONS_BY_TAXONOMY_TOOL = {
    "_contexts": {"rca_with_lessons"},
    "name": "lookup_lessons_by_taxonomy",
    "description": (
        "Return additional distilled lessons that share a given taxonomy "
        "code, ranked by the same composite scorer used to fill the lesson "
        "reference table.  The single representative already shown in that "
        "table is excluded from the results.  Use this when the row's "
        "representative is insufficient and you want a second opinion."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "taxonomy_tag": {
                "type": "string",
                "enum": ALL_SUBTYPES + TAXONOMY_CATEGORIES,
                "description": (
                    "Taxonomy code to look up (e.g. 'G1', 'R10').  Sub-type "
                    "codes give the most useful results."
                ),
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "How many additional lessons to return (default 3).",
            },
        },
        "required": ["taxonomy_tag"],
    },
}


SEARCH_LESSONS_BY_APP_TOOL = {
    "_contexts": {"rca_with_lessons"},
    "name": "search_lessons_by_app",
    "description": (
        "Return lessons drawn from a specific app, optionally restricted to "
        "a single taxonomy code, ranked by composite score.  Use this when "
        "you have formed a hypothesis about both which app and which failure "
        "category the current trajectory falls into."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "app_id": {
                "type": "string",
                "description": (
                    "Application identifier (e.g. 'chrome', 'vs_code').  "
                    "Use the same identifier that the trajectory directory's "
                    "parent folder carries."
                ),
            },
            "taxonomy_tag": {
                "type": "string",
                "enum": ALL_SUBTYPES + TAXONOMY_CATEGORIES,
                "description": "Optional taxonomy filter; omit to scan all codes.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "How many lessons to return (default 3).",
            },
        },
        "required": ["app_id"],
    },
}


FOLLOW_EPISODIC_REF_TOOL = {
    "_contexts": {"rca_with_lessons"},
    "name": "follow_episodic_ref",
    "description": (
        "Resolve an episodic_ref UUID into a compact summary of the original "
        "failed trajectory: task_id, app_id, taxonomy_tag, error_step and a "
        "small slice of the error context.  Use this when a lesson's text is "
        "abstract and you need to see the concrete failure it was distilled "
        "from."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "episodic_ref": {
                "type": "string",
                "description": (
                    "The UUID string carried in a lesson's episodic_refs list."
                ),
            },
        },
        "required": ["episodic_ref"],
    },
}


# ---------------------------------------------------------------------------
# Registry — definition order matters for the pre-built lists below.
# ---------------------------------------------------------------------------

_TOOL_REGISTRY = [
    LOAD_TRAJECTORY_TOOL,
    GET_STEP_DETAILS_TOOL,
    GENERAL_FINISH_TOOL,
    RCA_FINISH_TOOL,
    PROPOSE_ANNOTATION_TOOL,
    LOOKUP_LESSONS_BY_TAXONOMY_TOOL,
    SEARCH_LESSONS_BY_APP_TOOL,
    FOLLOW_EPISODIC_REF_TOOL,
]


# Pre-built convenience lists (identical contract to the old module).
TOOLS = get_tools("general")
RCA_TOOLS = get_tools("rca")
RCA_WITH_LESSONS_TOOLS = get_tools("rca_with_lessons")
DISCUSSION_TOOLS = get_tools("discussion")


__all__ = [
    # descriptors
    "LOAD_TRAJECTORY_TOOL",
    "GET_STEP_DETAILS_TOOL",
    "GENERAL_FINISH_TOOL",
    "RCA_FINISH_TOOL",
    "PROPOSE_ANNOTATION_TOOL",
    "LOOKUP_LESSONS_BY_TAXONOMY_TOOL",
    "SEARCH_LESSONS_BY_APP_TOOL",
    "FOLLOW_EPISODIC_REF_TOOL",
    # context helper
    "get_tools",
    # pre-built lists
    "TOOLS",
    "RCA_TOOLS",
    "RCA_WITH_LESSONS_TOOLS",
    "DISCUSSION_TOOLS",
]