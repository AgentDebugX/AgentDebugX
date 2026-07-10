"""Seed failure taxonomy for AgentDebugX.

This module starts with research-grounded failure modes and is designed to be
extended by generated, project-specific taxonomy nodes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from agentdebug.core.models import FailureMode


def _mode(
    mode_id: str,
    name: str,
    family: str,
    description: str,
    signals: List[str],
    suggestions: List[str],
    source: str,
) -> FailureMode:
    return FailureMode(
        mode_id=mode_id,
        name=name,
        family=family,
        description=description,
        signals=signals,
        suggestion_templates=suggestions,
        source=source,
    )


SEED_FAILURE_MODES: Dict[str, FailureMode] = {
    'memory.retrieval_failure': _mode(
        'memory.retrieval_failure',
        'Memory retrieval failure',
        'memory',
        'The agent failed to retrieve relevant prior state, observation, or task context.',
        ['missing context', 'forgot', 'stale context', 'retrieval miss'],
        [
            'Persist the missing state as structured memory and attach it to the next planning step.',
            'Add a retrieval quality check before acting on retrieved context.',
        ],
        'AgentDebug',
    ),
    'memory.hallucination': _mode(
        'memory.hallucination',
        'Memory hallucination',
        'memory',
        'The agent treats unobserved or invented state as remembered fact.',
        ['invented memory', 'unsupported recall', 'false prior state'],
        [
            'Require memory reads to cite the source event or artifact before use.',
            'Separate observed state from inferred state in the agent prompt.',
        ],
        'AgentDebug',
    ),
    'reflection.progress_misjudge': _mode(
        'reflection.progress_misjudge',
        'Progress misjudge',
        'reflection',
        'The agent incorrectly judges whether the task is solved or whether progress was made.',
        ['premature success', 'incorrect self evaluation', 'progress misjudge'],
        [
            'Add an external task verifier before termination.',
            'Record explicit success criteria and compare the current state against each criterion.',
        ],
        'AgentDebug',
    ),
    'reflection.causal_misattribution': _mode(
        'reflection.causal_misattribution',
        'Causal misattribution',
        'reflection',
        'The agent explains failure using the wrong cause and then optimizes the wrong behavior.',
        ['wrong root cause', 'misattribution', 'incorrect blame'],
        [
            'Use counterfactual replay or ablation to test whether the suspected step caused the failure.',
            'Preserve evidence links for each causal claim in the reflection.',
        ],
        'AgentDebug',
    ),
    'planning.constraint_ignorance': _mode(
        'planning.constraint_ignorance',
        'Constraint ignorance',
        'planning',
        'The plan ignores task, environment, policy, schema, or safety constraints.',
        ['constraint ignored', 'policy violation', 'cannot satisfy requirement'],
        [
            'Compile task and tool constraints into pre-action checks.',
            'Fail closed when required constraints cannot be verified.',
        ],
        'AgentDebug / MAST',
    ),
    'planning.impossible_action': _mode(
        'planning.impossible_action',
        'Impossible action',
        'planning',
        'The agent plans an action that cannot be executed in the current environment.',
        ['impossible action', 'unavailable operation', 'invalid transition'],
        [
            'Expose environment affordances as a machine-readable action space.',
            'Ask the planner to replan from the last valid state after an impossible action is detected.',
        ],
        'AgentDebug',
    ),
    'planning.inefficient_plan': _mode(
        'planning.inefficient_plan',
        'Inefficient plan',
        'planning',
        'The agent loops, over-decomposes, or burns steps without increasing task completion probability.',
        ['loop', 'step explosion', 'no progress', 'repeated action'],
        [
            'Add loop detection over tool calls and state deltas.',
            'Trigger a replan when the same state-action pattern repeats.',
        ],
        'AgentDebug / MAST',
    ),
    'action.wrong_tool': _mode(
        'action.wrong_tool',
        'Wrong tool selection',
        'action',
        'The agent selects a tool that does not match the task intent or current state.',
        ['wrong tool', 'unknown tool', 'tool mismatch'],
        [
            'Add tool routing examples and negative examples for similar tools.',
            'Use a tool-selection verifier before executing high-impact actions.',
        ],
        'AgentDebug / AgentRx',
    ),
    'action.invalid_action': _mode(
        'action.invalid_action',
        'Invalid action',
        'action',
        'The selected action is syntactically or semantically invalid for the environment.',
        ['invalid action', 'bad command', 'action rejected'],
        [
            'Validate action schemas before execution and return actionable repair hints.',
            'Record rejected actions to fine-tune tool descriptions and examples.',
        ],
        'AgentDebug',
    ),
    'action.format_error': _mode(
        'action.format_error',
        'Format error',
        'action',
        'The agent produced malformed JSON, arguments, commands, or protocol messages.',
        ['json parse', 'schema validation', 'malformed', 'format error'],
        [
            'Enforce structured output with schema validation and retry using validation errors.',
            'Prefer typed tool APIs over free-form command strings where possible.',
        ],
        'AgentDebug / AgentRx',
    ),
    'action.parameter_error': _mode(
        'action.parameter_error',
        'Parameter error',
        'action',
        'The agent called the right tool with missing, hallucinated, or invalid parameters.',
        ['missing parameter', 'invalid parameter', 'hallucinated argument'],
        [
            'Validate parameters against tool schemas and ask for missing user/context fields.',
            'Log parameter provenance so hallucinated arguments are visible in traces.',
        ],
        'AgentDebug / AgentRx',
    ),
    'system.tool_execution_error': _mode(
        'system.tool_execution_error',
        'Tool execution error',
        'system',
        'A tool, API, browser, environment, or dependency failed during execution.',
        ['timeout', 'exception', 'http error', 'tool failed', 'api error'],
        [
            'Capture tool stderr/status/latency and classify retryable versus non-retryable failures.',
            'Add idempotency keys and rollback logic for side-effecting tools.',
        ],
        'AgentDebug / AgentRx',
    ),
    'system.llm_limit': _mode(
        'system.llm_limit',
        'LLM limit',
        'system',
        'The model hit context, rate, content, or capability limits.',
        ['context length', 'rate limit', 'token limit', 'model refusal'],
        [
            'Summarize or shard long context with explicit loss accounting.',
            'Route limit failures to a fallback model or smaller prompt plan.',
        ],
        'AgentDebug',
    ),
    'system.environment_error': _mode(
        'system.environment_error',
        'Environment error',
        'system',
        'The external environment changed or returned noisy observations that misled the agent.',
        ['environment changed', 'noisy observation', 'browser state', 'missing element'],
        [
            'Log environment snapshots and compare state before and after actions.',
            'Use stable APIs or accessibility trees when visual/browser observations are brittle.',
        ],
        'AgentDebug / AgentSight',
    ),
    'multiagent.handoff_loss': _mode(
        'multiagent.handoff_loss',
        'Handoff context loss',
        'multiagent',
        'A receiving agent loses task constraints, rejected alternatives, or decision rationale.',
        ['handoff missing context', 'lost rationale', 'context handoff'],
        [
            'Make handoff payloads typed and include goal, constraints, evidence, confidence, and open questions.',
            'Validate that the receiver can restate critical constraints before proceeding.',
        ],
        'MAST / Who&When',
    ),
    'multiagent.role_drift': _mode(
        'multiagent.role_drift',
        'Role drift',
        'multiagent',
        'An agent acts outside its assigned responsibility or duplicates another agent role.',
        ['role drift', 'responsibility overlap', 'agent conflict'],
        [
            'Define ownership boundaries and permitted actions per agent.',
            'Add supervisor checks for role violations and duplicate work.',
        ],
        'MAST / Who&When',
    ),
    'verification.missing_task_validation': _mode(
        'verification.missing_task_validation',
        'Missing task validation',
        'verification',
        'The system accepts task completion without an independent final-state check.',
        ['no verifier', 'unchecked final answer', 'missing validation'],
        [
            'Add final-state validation that is independent of the acting agent.',
            'Store task-specific success criteria in the trace before execution begins.',
        ],
        'MAST',
    ),
    'verification.premature_stop': _mode(
        'verification.premature_stop',
        'Premature stop',
        'verification',
        'The agent stops before satisfying the task or exits after hitting a hidden stop condition.',
        ['premature stop', 'early termination', 'max steps'],
        [
            'Differentiate success, abandonment, and max-step termination in the run schema.',
            'Trigger recovery planning when termination reason is not verified success.',
        ],
        'MAST / AgentDebug',
    ),
    'multimodal.perception_error': _mode(
        'multimodal.perception_error',
        'Multimodal perception error',
        'multimodal',
        'The agent misreads an image, UI, audio, video, or file artifact and acts on bad perception.',
        ['visual mismatch', 'ocr error', 'audio transcription error', 'ui element mismatch'],
        [
            'Store raw multimodal artifacts alongside extracted text and confidence scores.',
            'Use redundant perception channels such as accessibility trees plus screenshots for UI tasks.',
        ],
        'AgentDebugX proposed extension',
    ),
}


def get_failure_mode(mode_id: str) -> Optional[FailureMode]:
    return SEED_FAILURE_MODES.get(mode_id)


def list_failure_modes() -> List[FailureMode]:
    return list(SEED_FAILURE_MODES.values())
