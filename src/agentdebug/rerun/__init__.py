"""Rerun-stage APIs.

Rerun is the second half of the AgentDebugX loop: it packages a diagnosis,
checkpoint, and retry directive for an approved executor, then compares the
new branch with the original trajectory. This package intentionally does not
auto-replay arbitrary external tools.
"""

from agentdebug.rerun.branch import RerunBranch, RerunComparison, compare_branches
from agentdebug.rerun.evaluators import LocalProxyEvaluation, evaluate_local_proxy
from agentdebug.rerun.executors.base import RerunExecutor, RerunResult
from agentdebug.rerun.request import RerunCheckpoint, RerunDirective, RerunRequest
from agentdebug.rerun.workflow import (
    RerunPlan,
    RerunWorkflow,
    RerunWorkflowResult,
    build_rerun_request,
)

__all__ = [
    'LocalProxyEvaluation',
    'RerunBranch',
    'RerunCheckpoint',
    'RerunComparison',
    'RerunDirective',
    'RerunExecutor',
    'RerunPlan',
    'RerunRequest',
    'RerunResult',
    'RerunWorkflow',
    'RerunWorkflowResult',
    'build_rerun_request',
    'compare_branches',
    'evaluate_local_proxy',
]
