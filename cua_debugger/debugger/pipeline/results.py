"""Per-task result serialization and ingestion display.

IF1 (success-after-debug) is distinguished by `taxonomy_tag`, not directory.
"""

import json
from datetime import datetime
from pathlib import Path

from debugger import IngestionResult, soft_tag_candidates

from .runtime import log


def steps_to_dicts(ir: IngestionResult) -> list[dict]:
    """Convert ingestion steps to serialisable dicts."""
    return [
        {
            "step_num": s.step_num,
            "action_type": s.action_type,
            "action_code": s.action_code,
            "llm_tool_use": s.llm_tool_use,
            "error": s.error,
            "reward": s.reward,
            "done": s.done,
            "screenshot": str(s.screenshot_path) if s.screenshot_path else None,
        }
        for s in ir.trajectory
    ]


def print_ingestion(ir: IngestionResult, traj_dir: Path) -> None:
    """Pretty-print an ingestion result."""
    log.info(f"\n{'='*60}")
    log.info(f"  Directory : {traj_dir}")
    log.info(f"  Task ID   : {ir.task_id}")
    instr = ir.instruction
    log.info(f"  Instruction: {instr[:80]}..." if len(instr) > 80 else f"  Instruction: {instr}")
    log.info(f"  Format    : {ir.fmt}")
    if ir.is_infeasible:
        log.info(f"  Infeasible: YES (task designed to be impossible)")
    log.info(f"  Status    : {ir.status}")
    log.info(f"  Steps     : {len(ir.trajectory)}")
    log.info(f"  Terminal  : step {ir.terminal_step}")
    if ir.error_msg:
        log.info(f"  Error     : {ir.error_msg[:120]}")

    log.info(f"\n  Step details:")
    for s in ir.trajectory:
        flags = (" [ERR]" if s.error else "") + (" [DONE]" if s.done else "")
        log.info(f"    step {s.step_num:>3}: {s.action_type:<12} reward={s.reward}{flags}")
        if s.error:
            log.info(f"             error: {s.error[:100]}")

    if ir.status == "failure" and ir.trajectory:
        last = ir.trajectory[-1]
        candidates = soft_tag_candidates(
            action_type=last.action_type,
            app_id=traj_dir.parent.name,
            visual_delta=0.0,
        )
        log.info(f"\n  Soft-tag candidates (heuristic, no LLM):")
        for tag, prob in candidates[:3]:
            log.info(f"    {tag}: {prob:.2f}")

    log.info(f"{'='*60}")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _summary_to_dict(summary) -> dict | None:
    if hasattr(summary, "model_dump"):
        return summary.model_dump(mode="python")
    if isinstance(summary, dict):
        return summary
    return None


def _merge_step_summaries(steps: list[dict], summaries: list[dict]) -> list[dict]:
    """Attach debugger-inspected summaries to their matching step entries."""
    by_step = {}
    for summary in summaries or []:
        summary = _summary_to_dict(summary)
        if summary is None:
            continue
        try:
            step_num = int(summary.get("step_num"))
        except (TypeError, ValueError):
            continue
        by_step[step_num] = {
            "intent_summary": str(summary.get("intent_summary", "")).strip(),
            "outcome_summary": str(summary.get("outcome_summary", "")).strip(),
            "summary_source": summary.get("summary_source") or "debugger_inspected",
        }

    for step in steps:
        summary = by_step.get(step["step_num"])
        if summary:
            step.update(summary)
    return steps


def _relative_to_trial(path: Path, trial_dir: Path) -> str:
    try:
        return str(path.relative_to(trial_dir)).replace("\\", "/")
    except ValueError:
        return str(path)


def _upsert_summary_index(index_path: Path, entry: dict) -> None:
    entries = []
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                existing.get("task_id") == entry.get("task_id")
                and existing.get("app_id") == entry.get("app_id")
            ):
                continue
            entries.append(existing)

    entries.append(entry)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


def _save_step_summary_result(
    trial_dir: Path,
    *,
    ir: IngestionResult,
    rca,
    model: str,
    traj_dir: Path,
    rca_file: Path,
    per_step_summaries: list[dict],
    created_at: str,
) -> None:
    if not per_step_summaries:
        return

    app_id = traj_dir.parent.name
    summary_file = trial_dir / "step_summaries" / app_id / f"{ir.task_id}.json"
    payload = {
        "schema_version": "per_step_summary.v1",
        "task_id": ir.task_id,
        "app_id": app_id,
        "debugger_model": model,
        "traj_path": str(traj_dir.resolve()),
        "rca_path": _relative_to_trial(rca_file, trial_dir),
        "root_error_step": rca.root_error_step,
        "taxonomy_tag": rca.taxonomy_tag,
        "evidence": rca.evidence,
        "correction": rca.correction,
        "confidence": rca.confidence,
        "coverage": "debugger_inspected_steps_only",
        "steps": per_step_summaries,
        "created_at": created_at,
    }
    _write_json(summary_file, payload)

    index_entry = {
        "task_id": ir.task_id,
        "app_id": app_id,
        "summary_path": _relative_to_trial(summary_file, trial_dir),
        "rca_path": _relative_to_trial(rca_file, trial_dir),
        "traj_path": str(traj_dir.resolve()),
        "debugger_model": model,
        "root_error_step": rca.root_error_step,
        "taxonomy_tag": rca.taxonomy_tag,
        "confidence": rca.confidence,
        "step_count": len(per_step_summaries),
        "created_at": created_at,
    }
    _upsert_summary_index(trial_dir / "step_summaries" / "index.jsonl", index_entry)


def save_rca_result(trial_dir: Path, ir: IngestionResult, rca, model: str,
                    traj_dir: Path) -> Path:
    rca_file = trial_dir / "rca" / f"rca_{ir.task_id}.json"
    per_step_summaries = [
        summary for summary in (
            _summary_to_dict(s) for s in getattr(rca, "per_step_summaries", [])
        )
        if summary is not None
    ]
    steps = _merge_step_summaries(steps_to_dicts(ir), per_step_summaries)
    created_at = datetime.now().isoformat()
    _write_json(rca_file, {
        "task_id": ir.task_id,
        "instruction": ir.instruction,
        "app_id": traj_dir.parent.name,
        "traj_path": str(traj_dir.resolve()),
        "status": ir.status,
        "is_infeasible": ir.is_infeasible,
        "total_steps": len(ir.trajectory),
        "terminal_step": ir.terminal_step,
        "root_error_step": rca.root_error_step,
        "taxonomy_tag": rca.taxonomy_tag,
        "evidence": rca.evidence,
        "correction": rca.correction,
        "confidence": rca.confidence,
        "per_step_summaries": per_step_summaries,
        "model": model,
        "created_at": created_at,
        "steps": steps,
    })
    _save_step_summary_result(
        trial_dir,
        ir=ir,
        rca=rca,
        model=model,
        traj_dir=traj_dir,
        rca_file=rca_file,
        per_step_summaries=per_step_summaries,
        created_at=created_at,
    )
    log.info(f"  -> RCA saved to {rca_file}")
    return rca_file


def save_config(trial_dir: Path, provider: str, model: str,
                trajectory_dir: Path) -> None:
    _write_json(trial_dir / "config.json", {
        "provider": provider,
        "model": model,
        "trajectory_dir": str(trajectory_dir),
        "created_at": datetime.now().isoformat(),
    })


def save_summary(trial_dir: Path, summary_entries: list[dict], provider: str,
                 model: str, trajectory_dir: Path) -> None:
    _write_json(trial_dir / "summary.json", {
        "trial": trial_dir.name,
        "model": model,
        "provider": provider,
        "trajectory_dir": str(trajectory_dir),
        "created_at": datetime.now().isoformat(),
        "total": len(summary_entries),
        "failures_analyzed": sum(1 for e in summary_entries
                                 if e["status"] == "failure" and e.get("taxonomy_tag")),
        "infeasible_analyzed": sum(1 for e in summary_entries if e.get("is_infeasible")),
        "success_after_debug": sum(1 for e in summary_entries
                                   if e["status"] == "success_after_debug"),
        "successes_skipped": sum(1 for e in summary_entries if e["status"] == "success"),
        "results": summary_entries,
    })
    log.info(f"\nSummary saved to {trial_dir / 'summary.json'}")


def save_classification(trial_dir: Path, classification: dict) -> Path:
    out_file = trial_dir / "classification.json"
    _write_json(out_file, {
        "created_at": datetime.now().isoformat(),
        "total": sum(len(v) for v in classification.values()),
        **{k: len(v) for k, v in classification.items()},
        "trajectories": classification,
    })
    log.info(f"Classification saved to {out_file}")
    return out_file
