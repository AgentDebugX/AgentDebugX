"""
Unified error taxonomy v2 for VLM-based agent trajectory debugging.

5 top-level categories, 31 sub-types (P1-P5, G1-G4, R1-R13, S1-S7, IF1-IF2).
"""

# Top-level categories
TAXONOMY_CATEGORIES = [
    "Perception",
    "Grounding & Interaction",
    "Task Reasoning & Control",
    "External / System",
    "Infeasible Task",
]

# Sub-type codes within each category
TAXONOMY_SUBTYPES = {
    "Perception": [
        "P1", "P2", "P3", "P4", "P5",
    ],
    "Grounding & Interaction": [
        "G1", "G2", "G3", "G4",
    ],
    "Task Reasoning & Control": [
        "R1", "R2", "R3", "R4", "R5", "R6", "R7",
        "R8", "R9", "R10", "R11", "R12", "R13",
    ],
    "External / System": [
        "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    ],
    "Infeasible Task": [
        "IF1", "IF2",
    ],
}

# Flat list of all valid sub-type codes
ALL_SUBTYPES = [code for codes in TAXONOMY_SUBTYPES.values() for code in codes]

# Category-level definitions
TAXONOMY_DEFINITIONS = {
    "Perception": (
        "The agent fails to correctly understand, interpret, or extract "
        "information from the visual screenshot or multimodal observation — "
        "errors in what the agent thinks it sees."
    ),
    "Grounding & Interaction": (
        "The agent fails to correctly locate, target, or physically interact "
        "with the intended element in the environment — errors in where and "
        "how the agent acts."
    ),
    "Task Reasoning & Control": (
        "The agent fails in higher-level cognitive processes — planning, task "
        "decomposition, memory management, self-reflection, tool/action "
        "selection, and control flow."
    ),
    "External / System": (
        "Failures from GUI rendering, environment instability, infrastructure "
        "limits, or benchmark artifacts — outside the agent's control."
    ),
    "Infeasible Task": (
        "The task is designed to be impossible to complete. Evaluates whether "
        "the agent correctly recognised the infeasibility or hallucinated "
        "feasibility and attempted the task anyway."
    ),
}

# Sub-type definitions
SUBTYPE_DEFINITIONS = {
    # Perception
    "P1": "Visual Hallucination — perceives objects, text, or UI elements not actually present.",
    "P2": "Misrecognition / OCR Error — content present but incorrectly identified or parsed.",
    "P3": "Cross-Modal Misbinding — incorrect association between information from different modalities or regions.",
    "P4": "Observation Omission — fails to attend to or notice necessary visible information.",
    "P5": "Semantic Misunderstanding — correctly perceives content but misinterprets its meaning.",

    # Grounding & Interaction
    "G1": "Coordinate / Element Grounding Error — targets wrong coordinates, DOM node, or spatial region.",
    "G2": "Visibility / Accessibility Error — element is off-screen, occluded, hidden, or disabled.",
    "G3": "Interaction Mechanics Error — wrong click type, missing drag, incorrect gesture or input method.",
    "G4": "Distraction / Adversarial Misdirection — targeting misdirected by ads, overlays, or decoys.",

    # Task Reasoning & Control — Planning & Reasoning
    "R1": "Constraint Violation — ignores explicit task constraints or requirements.",
    "R2": "Infeasible Plan / Impossible Action — plans actions that are logically or physically impossible.",
    "R3": "Decomposition Failure — task incorrectly broken into sub-goals (missing steps, wrong order).",
    "R4": "Inefficient / Redundant Strategy — valid plan but unnecessarily long or suboptimal.",

    # Task Reasoning & Control — Tool Call & Action Orchestration
    "R5": "Action-Intent Misalignment — executed action does not match the agent's stated plan.",
    "R6": "Invalid / Malformed Action — syntactically malformed action or calls non-existent API/tool.",
    "R7": "Parameter / Argument Error — correct action type but wrong arguments or parameters.",

    # Task Reasoning & Control — Memory & Context
    "R8": "Context Loss / Over-Simplification — fails to retain critical information from earlier steps.",
    "R9": "Memory Hallucination — asserts a false memory of a past observation or action.",

    # Task Reasoning & Control — Reflection & Control Flow
    "R10": "Progress Misjudgment — incorrectly assesses task completion (premature or fails to stop).",
    "R11": "Outcome Misinterpretation — misreads environment feedback about the result of the last action.",
    "R12": "Failed Self-Correction — recognizes error but applies incorrect or ineffective fix.",
    "R13": "Causal Misattribution — wrongly attributes failure to an incorrect cause.",

    # External / System — GUI / Environment
    "S1": "Rendering / Layout Failure — GUI does not render correctly or elements are misplaced.",
    "S2": "Timing / Race Condition — environment response timing causes valid action to fail.",
    "S3": "Unexpected System Behavior — OS dialogs, permission prompts, or notifications interfere.",

    # External / System — Infrastructure
    "S4": "Step / Resource Limit — viable strategy hits step, token, time, or rate limit.",
    "S5": "Tool / API Failure — external tool or API fails through no fault of the agent.",
    "S6": "Environment Instability — environment is buggy, non-deterministic, or crashes.",
    "S7": "Benchmark / Evaluation Artifact — ambiguous task spec, incorrect ground truth, or metric issue.",

    # Infeasible Task
    "IF1": "Infeasible Task Recognised — agent correctly identified the task as impossible and stopped.",
    "IF2": "Infeasible Task Not Recognised — agent hallucinated feasibility and attempted the impossible task.",
}

# Mapping from sub-type code to parent category
SUBTYPE_TO_CATEGORY = {}
for _cat, _codes in TAXONOMY_SUBTYPES.items():
    for _code in _codes:
        SUBTYPE_TO_CATEGORY[_code] = _cat

# Mapping from old v1 category names to v2 (for migration)
V1_TO_V2_CATEGORY = {
    "Perception-Level Errors": "Perception",
    "GUI-Level Errors": "Grounding & Interaction",
    "Memory Errors": "Task Reasoning & Control",
    "Reflection Errors": "Task Reasoning & Control",
    "Action Errors": "Task Reasoning & Control",
    "System-Level Failures (Non-agent)": "External / System",
}
