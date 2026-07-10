import importlib.util
import zipfile
from pathlib import Path

import pytest


def _load_downloader_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "download_input_trajectory.py"
    spec = importlib.util.spec_from_file_location("download_input_trajectory", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_huggingface_blob_url_is_converted_to_resolve_url():
    module = _load_downloader_module()
    assert module.huggingface_blob_to_resolve_url(
        "https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs/"
        "blob/main/claude-sonnet-4-5-20250929_50steps.zip"
    ) == (
        "https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs/"
        "resolve/main/claude-sonnet-4-5-20250929_50steps.zip"
    )


def test_safe_extract_zip_rejects_path_traversal(tmp_path):
    module = _load_downloader_module()
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "nope")

    with pytest.raises(ValueError, match="Unsafe zip member"):
        module.safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_zip_extracts_regular_members(tmp_path):
    module = _load_downloader_module()
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("trial/task/traj.jsonl", "{}\n")

    extracted = module.safe_extract_zip(archive, tmp_path / "out")

    assert (tmp_path / "out" / "trial" / "task" / "traj.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert extracted == [tmp_path / "out" / "trial" / "task" / "traj.jsonl"]


def test_safe_extract_zip_sanitizes_windows_member_names(tmp_path):
    module = _load_downloader_module()
    archive = tmp_path / "windows-name.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("trial/model:v1/traj.jsonl", "{}\n")

    extracted = module.safe_extract_zip(archive, tmp_path / "out")

    model_dir = "model_v1" if module.os.name == "nt" else "model:v1"
    expected = tmp_path / "out" / "trial" / model_dir / "traj.jsonl"
    assert expected.read_text(encoding="utf-8") == "{}\n"
    assert extracted == [expected]


def test_safe_extract_zip_handles_deep_member_paths(tmp_path):
    module = _load_downloader_module()
    archive = tmp_path / "deep.zip"
    deep_parts = [f"segment{i:02d}" for i in range(25)]
    member_name = "/".join(["trial", *deep_parts, "traj.jsonl"])
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member_name, "{}\n")

    extracted = module.safe_extract_zip(archive, tmp_path / "out")

    expected = tmp_path / "out" / "trial" / Path(*deep_parts) / "traj.jsonl"
    with open(module._windows_long_path(expected), encoding="utf-8") as f:
        assert f.read() == "{}\n"
    assert extracted == [expected]
