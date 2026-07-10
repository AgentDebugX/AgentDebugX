from pathlib import Path

from debugger.memory import EpisodicMemory, TrajectoryType


def test_add_with_ec_fields(tmp_path: Path):
    mem = EpisodicMemory(data_file=tmp_path / "ep.json")
    rec_id = mem.add(
        trajectory={
            "task_id": "t1",
            "instruction": "open settings",
            "traj_dir": "/tmp/t1",
            "steps": [{"step_num": 0}, {"step_num": 1}, {"step_num": 2}],
        },
        traj_type=TrajectoryType.TYPE1,
        metadata={"app_id": "chrome"},
        error_context={"error_step": 1, "window": [0, 2], "steps": [{"step_num": 0}, {"step_num": 1}, {"step_num": 2}]},
        agent_intention="What it did: clicked X. What it changed: nothing.",
        taxonomy_tag="S2",
        error_step=1,
        annotation={"source": "human", "taxonomy_tag": "S2"},
    )
    rec = mem.read(rec_id)
    assert rec is not None
    assert rec["taxonomy_tag"] == "S2"
    assert rec["instruction"] == "open settings"
    assert rec["error_step"] == 1
    assert rec["agent_intention"].startswith("What it did")
    assert rec["error_context"]["error_step"] == 1
    assert rec["annotation"]["source"] == "human"


def test_add_without_ec_fields_is_backward_compatible(tmp_path: Path):
    mem = EpisodicMemory(data_file=tmp_path / "ep.json")
    rec_id = mem.add(
        trajectory={"task_id": "t2", "traj_dir": "/tmp/t2", "steps": []},
        traj_type=TrajectoryType.TYPE2,
    )
    rec = mem.read(rec_id)
    # New fields default to None / empty, not missing
    assert rec["taxonomy_tag"] is None
    assert rec["error_context"] is None
    assert rec["agent_intention"] is None
    assert rec["error_step"] is None
    assert rec["annotation"] is None
