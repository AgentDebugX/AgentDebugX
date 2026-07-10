"""Case enumeration for evolving-taxonomy experiments.

Task IDs are read from ``human_*.json`` annotation files and intersected
with trajectory directories discovered under the configured trajectory root.
The default annotation path points under ``results/debugger_results``; pass
explicit paths for real experiments.
"""
from __future__ import annotations
import json
import random
from pathlib import Path

from debugger.config import load_config
from debugger.pipeline.classify import discover_trajectories

ANNOTATIONS_DIR = Path("results/debugger_results/annotations")

_cfg = load_config()


def _annotated_task_ids(annotations_dir: Path) -> set[str]:
    out: set[str] = set()
    if not annotations_dir.exists():
        raise FileNotFoundError(
            f"Annotations directory not found: {annotations_dir}. "
            f"Pass annotations_dir explicitly for external experiment data."
        )
    for f in annotations_dir.glob("human_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        tid = data.get("task_id")
        if tid:
            out.add(tid)
    return out


def _traj_dirs_by_task_id(trajectory_dir: Path) -> dict[str, Path]:
    """Map task_id (last path component) -> traj_dir for the configured trial."""
    out: dict[str, Path] = {}
    for d in discover_trajectories(trajectory_dir):
        # If multiple traj dirs share the same task_id (multi-app paths), the
        # first one wins; this matches the existing pipeline's behavior.
        out.setdefault(d.name, d)
    return out


def load_case_set(
    annotations_dir: Path | None = None,
    trajectory_dir: Path | None = None,
) -> list[str]:
    """Return a sorted list of task_ids that BOTH have a human annotation AND a
    discoverable trajectory directory.
    """
    ann = annotations_dir or ANNOTATIONS_DIR
    traj_root = trajectory_dir or Path(_cfg.trajectory_dir)
    annotated = _annotated_task_ids(ann)
    available = set(_traj_dirs_by_task_id(traj_root).keys())
    return sorted(annotated & available)


def resolve_traj_dirs(
    task_ids: list[str],
    trajectory_dir: Path | None = None,
) -> dict[str, Path]:
    """Resolve a list of task_ids to their on-disk trajectory directories."""
    traj_root = trajectory_dir or Path(_cfg.trajectory_dir)
    by_tid = _traj_dirs_by_task_id(traj_root)
    return {tid: by_tid[tid] for tid in task_ids if tid in by_tid}


def shuffle_for_seed(cases: list[str], seed: int) -> list[str]:
    """Return a new list shuffled deterministically by `seed`. Input is not mutated."""
    out = list(cases)
    random.Random(seed).shuffle(out)
    return out
