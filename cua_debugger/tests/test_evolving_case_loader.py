"""Case enumeration and seeded shuffle tests."""
import json

from debugger.evolving.case_loader import load_case_set, shuffle_for_seed


def _write_annotation(annotations_dir, task_id):
    annotations_dir.mkdir(parents=True, exist_ok=True)
    (annotations_dir / f"human_{task_id}.json").write_text(
        json.dumps({"task_id": task_id}), encoding="utf-8"
    )


def _write_traj(trajectory_dir, task_id):
    task_dir = trajectory_dir / "sample_app" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "traj.jsonl").write_text(
        '{"step_num": 1, "action": "noop"}\n', encoding="utf-8"
    )


def test_load_case_set_nonempty_and_unique(tmp_path):
    annotations_dir = tmp_path / "annotations"
    trajectory_dir = tmp_path / "trajectories"
    _write_annotation(annotations_dir, "task-a")
    _write_annotation(annotations_dir, "task-b")
    _write_traj(trajectory_dir, "task-a")

    cases = load_case_set(annotations_dir=annotations_dir, trajectory_dir=trajectory_dir)

    assert cases == ["task-a"]
    assert len(set(cases)) == len(cases)


def test_load_case_set_subset_of_annotations(tmp_path):
    annotations_dir = tmp_path / "annotations"
    trajectory_dir = tmp_path / "trajectories"
    _write_annotation(annotations_dir, "task-a")
    _write_annotation(annotations_dir, "task-b")
    _write_traj(trajectory_dir, "task-a")

    cases = set(load_case_set(annotations_dir=annotations_dir, trajectory_dir=trajectory_dir))
    disk_ids = set()
    for f in annotations_dir.glob("human_*.json"):
        disk_ids.add(json.loads(f.read_text(encoding="utf-8"))["task_id"])

    assert cases.issubset(disk_ids)
    assert cases == {"task-a"}


def test_shuffle_for_seed_is_deterministic():
    cs = ["a", "b", "c", "d", "e", "f"]
    assert shuffle_for_seed(cs, 0) == shuffle_for_seed(cs, 0)


def test_shuffle_for_seed_differs_across_seeds():
    cs = ["a", "b", "c", "d", "e", "f"]
    assert shuffle_for_seed(cs, 0) != shuffle_for_seed(cs, 1)


def test_shuffle_does_not_mutate_input():
    cs = ["a", "b", "c", "d", "e", "f"]
    snapshot = list(cs)
    _ = shuffle_for_seed(cs, 0)
    assert cs == snapshot
