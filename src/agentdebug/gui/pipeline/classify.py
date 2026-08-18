"""Trajectory discovery and success/failure classification by result.txt."""

from pathlib import Path

from .runtime import log


def discover_trajectories(trajectory_dir: Path) -> list[Path]:
    """Find all trajectory directories under trajectory_dir."""
    dirs = set()
    for name in ("trajectory.jsonl", "traj.jsonl"):
        dirs.update(p.parent for p in trajectory_dir.rglob(name))
    return sorted(dirs)


def classify_trajectories(trajectory_dir: Path) -> dict:
    """Classify trajectories by result.txt (success=1.0, failure=other, skipped=missing/invalid)."""
    classification = {"success": [], "failure": [], "skipped": []}

    traj_dirs = discover_trajectories(trajectory_dir)
    if not traj_dirs:
        log.info(f"No trajectories found in {trajectory_dir}")
        return classification

    for traj_dir in traj_dirs:
        result_file = traj_dir / "result.txt"
        if not result_file.exists():
            log.info(f"  [classify] No result.txt, skipping: {traj_dir.name}")
            classification["skipped"].append(str(traj_dir))
            continue

        try:
            result_val = float(result_file.read_text().strip())
        except ValueError:
            log.info(f"  [classify] Invalid result.txt, skipping: {traj_dir.name}")
            classification["skipped"].append(str(traj_dir))
            continue

        bucket = "success" if result_val == 1.0 else "failure"
        classification[bucket].append(str(traj_dir))

    skipped_count = len(classification["skipped"])
    log.info(f"  [classify] {len(classification['failure'])} failures, "
             f"{len(classification['success'])} successes"
             f"{f', {skipped_count} skipped' if skipped_count else ''}")
    return classification
