import json
from pathlib import Path


def test_annotation_only_trial_loads_tasks_and_trajectory(tmp_path, monkeypatch):
    from debugger.vis import debugger_app as app

    output_dir = tmp_path / "debugger_results"
    trial_dir = output_dir / "trial"
    ann_dir = trial_dir / "annotations"
    ann_dir.mkdir(parents=True)

    traj_dir = tmp_path / "input_trajectory" / "app" / "task-1"
    traj_dir.mkdir(parents=True)
    (traj_dir / "traj.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "step_num": 1,
                        "action": {
                            "action_type": "tool_use",
                            "command": "click",
                            "raw_response": "[THINKING] The user wants me to open the settings panel. I can start from the menu.",
                            "input": {},
                        },
                        "reward": 0,
                        "done": False,
                    }
                ),
                json.dumps({"step_num": 2, "action": "type", "reward": 0, "done": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (trial_dir / "classification.json").write_text(
        json.dumps(
            {
                "trajectories": {
                    "failure": [str(traj_dir)],
                    "success": [],
                    "skipped": [],
                }
            }
        ),
        encoding="utf-8",
    )
    (ann_dir / "human_task-1.json").write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "trial": "trial",
                "llm_values": {
                    "root_error_step": 2,
                    "taxonomy_tag": "R8",
                    "evidence": "evidence",
                    "correction": "correction",
                    "confidence": 0.9,
                },
                "human_values": [
                    {
                        "annotator": "A",
                        "root_error_step": 2,
                        "taxonomy_tag": "R8",
                        "evidence": "human evidence",
                        "correction": "human correction",
                        "confidence": "high",
                    }
                ],
                "notes": "",
            }
        ),
        encoding="utf-8",
    )
    legacy_orphan = ann_dir / "human_orphan.json"
    legacy_orphan_text = json.dumps(
        {
            "task_id": "orphan",
            "root_cause": "perception_error",
            "evidence": "legacy file should not be migrated by list loading",
            "label": "Perception.VisualMisread",
        },
        indent=2,
    )
    legacy_orphan.write_text(legacy_orphan_text, encoding="utf-8")

    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)

    assert app._discover_agent_trial_dirs() == [trial_dir]
    results = app.load_rca_results_for_agent(trial_dir)
    assert legacy_orphan.read_text(encoding="utf-8") == legacy_orphan_text

    assert len(results) == 1
    assert results[0]["task_id"] == "task-1"
    assert results[0]["traj_path"] == str(traj_dir)
    assert results[0]["app_id"] == "app"
    assert results[0]["instruction"] == "Open the settings panel."
    assert results[0]["total_steps"] == 2
    full_traj = app.load_full_trajectory(results[0]["traj_path"])
    assert full_traj["instruction"] == "Open the settings panel."
    assert len(full_traj["steps"]) == 2


def test_step_status_display_uses_plain_readable_text():
    from debugger.vis.debugger_app import step_status_display

    assert step_status_display(1, 2, False) == ("normal-step", "OK", "Step 1 - OK")
    assert step_status_display(2, 2, False) == ("root-step", "ROOT CAUSE", "Step 2 - ROOT CAUSE")
    assert step_status_display(3, 2, True) == ("cascade-step", "CASCADED", "Step 3 - CASCADED")
    assert step_status_display(1, 2, True) == ("cascade-step", "ERROR", "Step 1 - ERROR")
