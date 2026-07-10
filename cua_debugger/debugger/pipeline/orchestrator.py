"""Top-level pipeline orchestration: classify -> ingest -> RCA -> tagger -> memory."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from debugger.memory import EpisodicMemory

from .classify import classify_trajectories
from .client_factory import make_client
from .results import save_classification, save_config, save_summary
from .runtime import create_trial_dir, log, setup_logging
from .worker import process_task


def run_full_pipeline(trajectory_dir: Path, provider: str, model: str, base_url: str | None,
                      output_dir: Path, trial_name: str | None = None,
                      skip_existing: bool = False,
                      workers: int = 4,
                      trail_name: str | None = None,
                      num: int | None = None) -> None:
    """Full pipeline: classify -> ingest -> RCA -> tagger -> memory.

    trail_name: appended to the debugger subdir name (e.g. "v2" → gemini-3-flash-v2/).
    num:        process only the first N failure trajectories (after sort).
    """

    # ── Create trial directory ──
    trial_dir = create_trial_dir(output_dir, trial_name, debugger_model=model,
                                 trail_name=trail_name)
    agent_trial_dir = trial_dir.parent  # AGENT-level dir (annotations/ + classification.json live here)
    setup_logging(trial_dir)
    log.info(f"Trial directory (debugger-scoped): {trial_dir}")
    log.info(f"Agent trial dir (shared): {agent_trial_dir}")

    save_config(trial_dir, provider, model, trajectory_dir)

    # ── Phase 0: Classify trajectories (no file moves) ──
    log.info("Classifying trajectories...")
    classification = classify_trajectories(trajectory_dir)
    save_classification(agent_trial_dir, classification)

    client = make_client(provider, model, base_url)
    osworld_root = Path(".")
    mem = EpisodicMemory(data_file=trial_dir / "episodic.json")

    # Only process failure trajectories.
    # Infeasible tasks that succeeded are skipped — OSWorld guarantees
    # success + infeasible means the agent recognised infeasibility.
    failure_paths = set(classification["failure"])
    traj_dirs = sorted(Path(p) for p in failure_paths)
    if not traj_dirs:
        log.info("No failure trajectories to analyze.")
        return

    if num is not None and num > 0:
        log.info(f"--num {num}: trimming {len(traj_dirs)} → {min(num, len(traj_dirs))} trajectories")
        traj_dirs = traj_dirs[:num]

    total = len(traj_dirs)
    log.info(f"Found {total} failure trajectories to analyze "
             f"({len(classification['success'])} successes skipped)")
    log.info(f"Provider: {provider}")
    log.info(f"Model: {model}")
    log.info(f"Workers: {workers}\n")

    # ── Process tasks (parallel when workers > 1) ──
    summary_entries: list[dict] = []

    if workers <= 1:
        # Sequential mode (preserves old behavior)
        for idx, traj_dir in enumerate(traj_dirs, 1):
            entry = process_task(
                traj_dir, trial_dir, model, client, osworld_root,
                mem, skip_existing, idx, total,
            )
            if entry is not None:
                summary_entries.append(entry)
    else:
        # Parallel mode
        log.info(f"Starting thread pool with {workers} workers...")
        completed = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rca") as executor:
            futures = {
                executor.submit(
                    process_task,
                    traj_dir, trial_dir, model, client, osworld_root,
                    mem, skip_existing, idx, total,
                ): traj_dir
                for idx, traj_dir in enumerate(traj_dirs, 1)
            }
            for future in as_completed(futures):
                traj_dir = futures[future]
                try:
                    entry = future.result()
                    if entry is not None:
                        summary_entries.append(entry)
                        completed += 1
                    else:
                        failed += 1
                except Exception as e:
                    log.info(f"[WORKER ERROR] {traj_dir.name}: {e}")
                    failed += 1

        log.info(f"\nAll workers finished: {completed} completed, {failed} failed")

    # ── Update classification with success_after_debug ──
    if1_task_ids = {e["task_id"] for e in summary_entries if e.get("status") == "success_after_debug"}
    if if1_task_ids:
        # Move IF1 trajectories from failure to success_after_debug
        classification["success_after_debug"] = []
        remaining_failures = []
        for path_str in classification["failure"]:
            task_id = Path(path_str).name
            if task_id in if1_task_ids:
                classification["success_after_debug"].append(path_str)
            else:
                remaining_failures.append(path_str)
        classification["failure"] = remaining_failures
        save_classification(agent_trial_dir, classification)
        log.info(f"  [classify] Moved {len(if1_task_ids)} IF1 tasks to success_after_debug")

    # ── Save summary ──
    save_summary(trial_dir, summary_entries, provider, model, trajectory_dir)

    # ── Print memory state ──
    all_records = mem.list()
    log.info(f"\n── Memory State ({len(all_records)} records) ──")
    for rec in all_records:
        log.info(f"  [{rec['type']}] {rec['task_id']} | {rec['app_id']} | "
                 f"{rec['steps_count']} steps | {rec['created_at'][:19]}")
