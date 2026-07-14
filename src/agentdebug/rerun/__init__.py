"""Rerun-stage APIs.

Rerun is the second half of the AgentDebugX loop: it packages a diagnosis,
checkpoint, and retry directive for an approved executor, then compares the
new branch with the original trajectory. This package intentionally does not
auto-replay arbitrary external tools.
"""

from agentdebug.rerun.branch import RerunBranch, RerunComparison, compare_branches
from agentdebug.rerun.actor_task import (
    ACTOR_TASK_RECORD_TYPE,
    ACTOR_TASK_SCHEMA_VERSION,
    ActorRerunTask,
    build_actor_rerun_task,
    export_actor_rerun_tasks,
)
from agentdebug.rerun.evaluators import LocalProxyEvaluation, evaluate_local_proxy
from agentdebug.rerun.executors import (
    LLMContinuationExecutor,
    HTTP_RUNNER_PROTOCOL_VERSION,
    HttpLiveExecutor,
    normalize_http_runner_url,
    LIVE_EXECUTION,
    ProcessLiveExecutor,
    RerunExecutor,
    RerunResult,
    RolloutContext,
    SimulatedRerunExecutor,
    SIMULATED_ROLLOUT,
    build_rollout_prompt,
    normalize_openai_base_url,
    trajectory_from_rollout,
)
from agentdebug.rerun.request import (
    RerunCapability,
    RerunCheckpoint,
    RerunDirective,
    RerunRequest,
)
from agentdebug.rerun.http_service import (
    HttpRunnerCapabilities,
    LiveRunner,
    create_http_runner_app,
    load_live_runner,
    serve_http_runner,
)
from agentdebug.rerun.workflow import (
    RerunPlan,
    RerunWorkflow,
    RerunWorkflowResult,
    assess_rerun_capability,
    build_rerun_request,
)

__all__ = [
    'ACTOR_TASK_RECORD_TYPE',
    'ACTOR_TASK_SCHEMA_VERSION',
    'ActorRerunTask',
    'LocalProxyEvaluation',
    'LLMContinuationExecutor',
    'HTTP_RUNNER_PROTOCOL_VERSION',
    'HttpLiveExecutor',
    'HttpRunnerCapabilities',
    'LIVE_EXECUTION',
    'LiveRunner',
    'ProcessLiveExecutor',
    'RerunBranch',
    'RerunCapability',
    'RerunCheckpoint',
    'RerunComparison',
    'RerunDirective',
    'RerunExecutor',
    'RerunPlan',
    'RerunRequest',
    'RerunResult',
    'RerunWorkflow',
    'RerunWorkflowResult',
    'RolloutContext',
    'SimulatedRerunExecutor',
    'SIMULATED_ROLLOUT',
    'build_rollout_prompt',
    'build_actor_rerun_task',
    'build_rerun_request',
    'assess_rerun_capability',
    'compare_branches',
    'create_http_runner_app',
    'evaluate_local_proxy',
    'export_actor_rerun_tasks',
    'load_live_runner',
    'normalize_openai_base_url',
    'normalize_http_runner_url',
    'trajectory_from_rollout',
    'serve_http_runner',
]
