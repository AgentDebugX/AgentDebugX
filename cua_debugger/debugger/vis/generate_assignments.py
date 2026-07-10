"""Generate annotation_assignments.json for a trial directory.

Evenly splits tasks (failure + success_after_debug) among annotators so each
person knows what to review.

Usage:
    python debugger/vis/generate_assignments.py <trial_dir> [--annotators a b c d e]

Example:
    python debugger/vis/generate_assignments.py results/debugger_results/test-1
    python debugger/vis/generate_assignments.py results/debugger_results/test-1 --annotators Alice Bob Carol Dave Eve
"""

import argparse
import json
from pathlib import Path


def generate_assignments(trial_dir: Path, annotators: list[str]) -> dict:
    """Read classification.json and split failure + success_after_debug tasks among annotators."""
    cls_file = trial_dir / "classification.json"
    if not cls_file.exists():
        raise FileNotFoundError(f"No classification.json in {trial_dir}")

    with open(cls_file, "r", encoding="utf-8") as f:
        classification = json.load(f)

    trajectories = classification.get("trajectories", {})
    # Collect task_ids from failure and success_after_debug categories
    task_ids = []
    for category in ("failure", "success_after_debug"):
        for path_str in trajectories.get(category, []):
            task_id = Path(path_str).name
            task_ids.append(task_id)
    task_ids.sort()

    if not task_ids:
        raise ValueError(f"No failure or success_after_debug tasks in {cls_file}")

    # Round-robin assignment
    assignments = {name: [] for name in annotators}
    annotator_list = list(annotators)
    for i, task_id in enumerate(task_ids):
        assignee = annotator_list[i % len(annotator_list)]
        assignments[assignee].append(task_id)

    result = {
        "trial": trial_dir.name,
        "annotators": annotators,
        "total_tasks": len(task_ids),
        "assignments": assignments,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate annotation assignments")
    parser.add_argument("trial_dir", type=Path, help="Path to trial directory")
    parser.add_argument(
        "--annotators", nargs="+",
        default=["person1", "person2", "person3", "person4", "person5"],
        help="List of annotator names (default: person1-person5)",
    )
    args = parser.parse_args()

    result = generate_assignments(args.trial_dir, args.annotators)

    out_path = args.trial_dir / "annotation_assignments.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved {out_path}")
    for name, tasks in result["assignments"].items():
        print(f"  {name}: {len(tasks)} tasks")


if __name__ == "__main__":
    main()
