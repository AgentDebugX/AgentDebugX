"""Per-task worker: ingest -> RCA -> tag -> memory store. Thread-safe."""

import json
import threading
from pathlib import Path

from debugger import ingest, run_rca
from debugger.memory import (
    EpisodicMemory,
    TrajectoryType,
    build_error_context,
    distill_lesson,
    extract_intention,
    lesson_to_dict,
    load_annotation,
)
from debugger.tagger import tag_from_rca

from .results import print_ingestion, save_rca_result, steps_to_dicts
from .runtime import log


def _rca_payload(rca) -> dict:
    return {
        "root_error_step": rca.root_error_step,
        "taxonomy_tag": rca.taxonomy_tag,
        "evidence": rca.evidence,
        "correction": rca.correction,
        "confidence": rca.confidence,
    }


def _traj_dict(ir, traj_dir: Path, rca=None, model: str | None = None) -> dict:
    d = {
        "task_id": ir.task_id,
        "instruction": ir.instruction,
        "traj_dir": str(traj_dir.resolve()),
        "steps": steps_to_dicts(ir),
    }
    if rca is not None:
        d["rca_analysis"] = _rca_payload(rca) | {"model": model}
    return d


def process_task(
    traj_dir: Path,
    trial_dir: Path,
    model: str,
    client,
    osworld_root: Path,
    mem: EpisodicMemory,
    skip_existing: bool,
    task_index: int,
    total_tasks: int,
) -> dict | None:
    """Process one trajectory (ingest -> RCA -> tag -> save).

    Returns a summary entry dict, or None on ingest failure.
    """
    tid = threading.current_thread().name
    prefix = f"[{tid}] [{task_index}/{total_tasks}]"

    log.info(f"\n{'#'*60}")
    log.info(f"{prefix} Processing: {traj_dir.name}")
    log.info(f"{'#'*60}")

    # ── Ingest ──
    try:
        ir = ingest(traj_dir)
        print_ingestion(ir, traj_dir)
    except Exception as e:
        log.info(f"{prefix} [INGEST FAIL] {e}")
        return None

    # ── Skip existing? ──
    rca_file = trial_dir / "rca" / f"rca_{ir.task_id}.json"
    if skip_existing and rca_file.exists():
        log.info(f"{prefix}   -> Skipping (result exists): {rca_file}")
        existing = json.loads(rca_file.read_text())
        entry = {
            "task_id": existing["task_id"],
            "status": existing["status"],
            "taxonomy_tag": existing.get("taxonomy_tag"),
            "root_error_step": existing.get("root_error_step"),
            "confidence": existing.get("confidence"),
        }
        if existing.get("taxonomy_tag") == "IF1":
            entry["status"] = "success_after_debug"
        if existing.get("is_infeasible"):
            entry["is_infeasible"] = True
        return entry

    if ir.status == "success":
        log.info(f"{prefix}   -> Task succeeded, skipping RCA. Storing as Type2.")
        mem.add(_traj_dict(ir, traj_dir), TrajectoryType.TYPE2,
                metadata={"app_id": traj_dir.parent.name})
        return {"task_id": ir.task_id, "status": "success"}

    # ── RCA ──
    if ir.is_infeasible:
        log.info(f"{prefix}   Running infeasible-task analysis "
                 f"(agent {'succeeded' if ir.status == 'success' else 'failed'})...")
    else:
        log.info(f"{prefix}   Running RCA (this may take a minute)...")
    try:
        log_path = trial_dir / "log" / f"{ir.task_id}.json"
        rca = run_rca(ir, model, client, osworld_root,
                      verbose=True, log_path=log_path,
                      app_id=traj_dir.parent.name)
    except Exception as e:
        log.info(f"{prefix}   [RCA FAIL] {e}")
        mem.add(_traj_dict(ir, traj_dir), TrajectoryType.TYPE1,
                metadata={"app_id": traj_dir.parent.name})
        return {"task_id": ir.task_id, "status": "failure", "error": str(e)[:200]}

    # ── Tag validation ──
    tag = tag_from_rca(rca)
    log.info(f"\n{prefix}   RCA Results:")
    log.info(f"    Root error step : {rca.root_error_step}")
    log.info(f"    Taxonomy tag    : {rca.taxonomy_tag}")
    log.info(f"    Validated tag   : {tag}")
    log.info(f"    Evidence        : {rca.evidence[:150]}")
    log.info(f"    Correction      : {rca.correction[:150]}")
    log.info(f"    Confidence      : {rca.confidence:.2f}")

    save_rca_result(trial_dir, ir, rca, model, traj_dir)

    # ── Annotation (human preferred) + intention + episodic store ──
    annotation = load_annotation(trial_dir.parent, ir.task_id)
    annotation_dict = None
    if annotation is not None:
        annotation_dict = {
            "source": annotation.source,
            "root_error_step": annotation.root_error_step,
            "taxonomy_tag": annotation.taxonomy_tag,
            "evidence": annotation.evidence,
            "correction": annotation.correction,
            "confidence": annotation.confidence,
        }

    error_step = annotation.root_error_step if annotation else rca.root_error_step
    ec_t = build_error_context(ir, error_step=error_step)
    try:
        intention = extract_intention(ec_t, client=client, model=model,
                                      instruction=ir.instruction)
    except Exception as e:
        log.info(f"{prefix}   [INTENTION FAIL] {e}")
        intention = ""

    episodic_id = mem.add(
        _traj_dict(ir, traj_dir, rca=rca, model=model),
        TrajectoryType.TYPE1,
        metadata={"app_id": traj_dir.parent.name},
        error_context=ec_t,
        agent_intention=intention,
        taxonomy_tag=rca.taxonomy_tag,
        error_step=error_step,
        annotation=annotation_dict,
    )

    # ── Distill Lesson ──
    try:
        lesson = distill_lesson(
            ec_t=ec_t, rca=_rca_payload(rca), intention=intention,
            client=client, model=model,
            app_id=traj_dir.parent.name, episodic_ref=episodic_id,
        )
        lessons_dir = trial_dir / "lessons"
        lessons_dir.mkdir(exist_ok=True)
        (lessons_dir / f"{ir.task_id}.json").write_text(
            json.dumps(lesson_to_dict(lesson), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"{prefix}   Lesson distilled: {lesson.title}")
    except Exception as e:
        log.info(f"{prefix}   [DISTILL FAIL] {e}")

    return {
        "task_id": ir.task_id,
        "status": "failure",
        "taxonomy_tag": rca.taxonomy_tag,
        "root_error_step": rca.root_error_step,
        "confidence": rca.confidence,
    }
