"""
Debugger Visualization â€” Streamlit app for viewing RCA results + raw trajectories.

Combines the debugger's RCA analysis with the original trajectory viewer so that
users can human-validate the LLM-as-judge debugger output against the raw data.

Usage:
    streamlit run debugger/vis/debugger_app.py

Reads from:
    - results/debugger_results/<trial>/rca/  (individual RCA JSON files)
    - results/debugger_results/<trial>/episodic.json (episodic memory)
    - <traj_path>/screenshots/               (step screenshots)
    - <traj_path>/traj.jsonl                 (raw trajectory data)
    - <traj_path>/a11y_trees/                (accessibility trees â€” LLM input)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw
from streamlit_adjustable_columns import adjustable_columns

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from debugger.config import load_config
from debugger.taxonomy import ALL_SUBTYPES, SUBTYPE_DEFINITIONS
from debugger.eval import compute_accuracy
from debugger.memory.annotation_loader import load_debugger_refs
from debugger.trajectory import infer_instruction_from_entry

CONFIDENCE_OPTIONS = ["low", "mid", "high"]

_cfg = load_config()
OUTPUT_DIR = PROJECT_ROOT / _cfg.output_dir

_REPO_NAME = PROJECT_ROOT.name
ANALYSIS_DIR = PROJECT_ROOT / "analysis_results"
_PATH_COMPONENT_ALIASES = {
    "results_qwen_100steps_baseline30": "qwen100steps_baseline30",
    "results_gemini50_baseline30": "gemini50_baseline30",
}


def _windows_long_path(path: str | Path) -> str:
    """Return a Windows extended-length path when needed."""
    resolved = str(Path(path).resolve(strict=False))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _path_exists(path: str | Path) -> bool:
    return os.path.exists(_windows_long_path(path))


def _has_annotation_artifact(d: Path) -> bool:
    return (
        (d / "annotations").exists()
        or (d / "annotation_assignments.json").exists()
        or (d / "classification.json").exists()
        or (d / "repeat").exists()
    )


def _remap_path(p: str | None) -> str | None:
    """Resolve any path to a local path under PROJECT_ROOT.

    Finds the current repo name in the path and maps
    everything after it relative to PROJECT_ROOT.
    On Windows, colons in path segments (e.g. 'v1:0') are replaced
    with underscores since Windows forbids ':' in filenames.
    """
    if not p:
        return p
    if _path_exists(p):
        return p
    # Normalize backslashes to forward slashes for consistent matching
    normalized = p.replace("\\", "/")
    marker = _REPO_NAME + "/"
    idx = normalized.find(marker)
    if idx != -1:
        rel = normalized[idx + len(marker):]
        # On Windows, colons are forbidden in filenames; repos cloned from
        # Linux may have had ':' replaced with '_' on disk.
        if os.name == "nt":
            rel = rel.replace(":", "_")
        candidate = str(PROJECT_ROOT / rel)
        if _path_exists(candidate):
            return candidate
        parts = Path(rel).parts
        aliased_parts = tuple(_PATH_COMPONENT_ALIASES.get(part, part) for part in parts)
        if aliased_parts != parts:
            alias_candidate = str(PROJECT_ROOT.joinpath(*aliased_parts))
            if _path_exists(alias_candidate):
                return alias_candidate
        return candidate
    return p

# â”€â”€ Page config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

st.set_page_config(layout="wide", page_title="Debugger Vis")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700;12..96,800&family=Figtree:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
/* â”€â”€ Base typography â”€â”€ */
.stApp { font-family: 'Figtree', sans-serif; }
h1, h2, h3, h4, h5, h6,
[data-testid="stHeadingWithActionElements"] {
    font-family: 'Bricolage Grotesque', sans-serif !important;
    letter-spacing: -0.02em;
}
code, pre, .stCode, [data-testid="stCode"] {
    font-family: 'JetBrains Mono', monospace !important;
}

/* â”€â”€ Scrollbar â”€â”€ */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(99,102,241,0.22); border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: rgba(99,102,241,0.4); }

/* â”€â”€ Tags â€” pill style â”€â”€ */
.tag {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 100px;
    font-size: 0.74em;
    font-weight: 600;
    margin-right: 6px;
    margin-bottom: 6px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border: 1px solid transparent;
    font-family: 'JetBrains Mono', monospace;
}

.tag-error   { background: #fef2f2; color: #b91c1c; border-color: #fecaca; }
.tag-ok      { background: #ecfdf5; color: #047857; border-color: #a7f3d0; }
.tag-warn    { background: #fffbeb; color: #92400e; border-color: #fde68a; }
.tag-info    { background: #eef2ff; color: #4338ca; border-color: #c7d2fe; }
.tag-purple  { background: #faf5ff; color: #6d28d9; border-color: #ddd6fe; }

/* â”€â”€ Step cards â”€â”€ */
.root-step {
    border-left: 3px solid #ef4444;
    padding: 18px 18px 18px 22px;
    background: linear-gradient(135deg, #fef2f2 0%, #fffafa 100%);
    border-radius: 10px;
    margin-bottom: 14px;
    box-shadow: 0 1px 4px rgba(239,68,68,0.07);
}
.cascade-step {
    border-left: 3px solid #f59e0b;
    padding: 18px 18px 18px 22px;
    background: linear-gradient(135deg, #fffbeb 0%, #fffefa 100%);
    border-radius: 10px;
    margin-bottom: 14px;
    box-shadow: 0 1px 4px rgba(245,158,11,0.07);
}
.normal-step {
    border-left: 3px solid #10b981;
    padding: 18px 18px 18px 22px;
    background: linear-gradient(135deg, #ecfdf5 0%, #f0fdf8 100%);
    border-radius: 10px;
    margin-bottom: 14px;
    box-shadow: 0 1px 4px rgba(16,185,129,0.07);
}

/* â”€â”€ Dark mode â”€â”€ */
@media (prefers-color-scheme: dark) {
    .tag-error   { background: #450a0a; color: #fca5a5; border-color: #7f1d1d; }
    .tag-ok      { background: #052e16; color: #86efac; border-color: #166534; }
    .tag-warn    { background: #451a03; color: #fde68a; border-color: #78350f; }
    .tag-info    { background: #1e1b4b; color: #a5b4fc; border-color: #3730a3; }
    .tag-purple  { background: #2e1065; color: #c4b5fd; border-color: #4c1d95; }
    .root-step   { border-left-color: #f87171; background: linear-gradient(135deg, #1c0606 0%, #200a0a 100%);
                   box-shadow: 0 1px 4px rgba(248,113,113,0.05); }
    .cascade-step{ border-left-color: #fbbf24; background: linear-gradient(135deg, #1c1206 0%, #201608 100%);
                   box-shadow: 0 1px 4px rgba(251,191,36,0.05); }
    .normal-step { border-left-color: #34d399; background: linear-gradient(135deg, #061c12 0%, #082016 100%);
                   box-shadow: 0 1px 4px rgba(52,211,153,0.05); }
}

/* â”€â”€ Discussion panel â”€â”€ */
.discuss-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 18px;
    background: linear-gradient(135deg, #4338ca 0%, #6366f1 50%, #818cf8 100%);
    color: white;
    border-radius: 10px;
    margin-bottom: 16px;
    font-weight: 700;
    font-size: 0.92em;
    font-family: 'Bricolage Grotesque', sans-serif;
    letter-spacing: -0.01em;
    box-shadow: 0 2px 10px rgba(99,102,241,0.25);
}
.discuss-header .discuss-icon { font-size: 1.2em; }

/* â”€â”€ Section divider â”€â”€ */
.section-divider {
    height: 1px;
    background: linear-gradient(90deg, transparent 0%, rgba(99,102,241,0.18) 50%, transparent 100%);
    margin: 28px 0;
    border: none;
}

/* â”€â”€ Instruction highlight â”€â”€ */
.instruction-box {
    padding: 16px 20px;
    border-radius: 10px;
    border-left: 3px solid #6366f1;
    background: linear-gradient(90deg, rgba(99,102,241,0.05) 0%, transparent 100%);
    font-size: 0.95em;
    line-height: 1.65;
    margin: 12px 0 16px 0;
}
@media (prefers-color-scheme: dark) {
    .instruction-box {
        background: linear-gradient(90deg, rgba(99,102,241,0.1) 0%, transparent 100%);
    }
}

/* â”€â”€ Sticky discussion column â”€â”€ */
[data-testid="stHorizontalBlock"] > div:last-child {
    position: sticky;
    top: 3.5rem;
    align-self: flex-start;
    max-height: calc(100vh - 4rem);
    overflow-y: auto;
    padding-left: 8px;
}

/* â”€â”€ Sidebar polish â”€â”€ */
[data-testid="stSidebar"] {
    border-right: 1px solid rgba(99,102,241,0.08);
}
[data-testid="stSidebar"] [data-testid="stMetric"] {
    background: rgba(99,102,241,0.04);
    padding: 12px;
    border-radius: 8px;
    border: 1px solid rgba(99,102,241,0.06);
}
</style>
""", unsafe_allow_html=True)


# â”€â”€ Data loading â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _find_latest_trial() -> Path | None:
    """Find the most recent debugger trial directory under OUTPUT_DIR."""
    trials = _discover_debugger_trial_dirs()
    return trials[0] if trials else None


def _debugger_trial_dirs_for_agent(agent_dir: str | Path) -> list[Path]:
    """Return debugger-scoped RCA dirs for one agent-level trial.

    Nested layout:
      <output>/<agent>/<debugger>/rca/

    Legacy flat layout:
      <output>/<trial>/rca/
    """
    p = Path(agent_dir)
    if (p / "rca").is_dir():
        return [p]
    if not p.is_dir():
        return []
    return _sort_by_name(d for d in p.iterdir() if d.is_dir() and (d / "rca").is_dir())


def _output_trial_roots() -> list[Path]:
    """Top-level result dirs under OUTPUT_DIR."""
    if not OUTPUT_DIR.exists():
        return []
    return [p for p in OUTPUT_DIR.iterdir() if p.is_dir()]


def _sort_by_name(paths) -> list[Path]:
    return sorted(paths, key=lambda p: p.name.lower())


def _sort_by_mtime(paths, key=None) -> list[Path]:
    return sorted(paths, key=lambda p: (key(p) if key else p.stat().st_mtime), reverse=True)


def _discover_debugger_trial_dirs() -> list[Path]:
    """Discover all debugger-scoped trial dirs under OUTPUT_DIR."""
    trials: list[Path] = []
    for top in _output_trial_roots():
        if (top / "rca").is_dir():
            trials.append(top)
        else:
            trials.extend(_debugger_trial_dirs_for_agent(top))
    return _sort_by_mtime(trials)


def _discover_agent_trial_dirs() -> list[Path]:
    """Discover agent-level trials, grouping sibling debugger runs together."""
    def has_agent_data(agent: Path) -> bool:
        return (
            _has_annotation_artifact(agent)
            or (agent / "rca").is_dir()
            or bool(_debugger_trial_dirs_for_agent(agent))
        )

    def newest_ref_mtime(agent: Path) -> float:
        debugger_dirs = _debugger_trial_dirs_for_agent(agent)
        candidates = [d.stat().st_mtime for d in debugger_dirs]
        for name in ("annotations", "annotation_assignments.json", "classification.json", "repeat"):
            p = agent / name
            if p.exists():
                candidates.append(p.stat().st_mtime)
        return max(candidates) if candidates else agent.stat().st_mtime

    return _sort_by_mtime(
        [top for top in _output_trial_roots() if has_agent_data(top)],
        key=newest_ref_mtime,
    )


def _select_trial(label: str, trials: list[Path]) -> str | None:
    """Render a sidebar trial selectbox and return the selected absolute path."""
    if not trials:
        return None
    trial_labels = [t.relative_to(OUTPUT_DIR).as_posix() for t in trials]
    sel_label = st.sidebar.selectbox(label, trial_labels)
    return str(OUTPUT_DIR / sel_label)


def load_rca_results(trial_dir: str | None = None):
    """Load individual RCA JSON files from trial_dir/rca/."""
    td = Path(trial_dir) if trial_dir else _find_latest_trial()
    if not td or not (td / "rca").exists():
        return []
    results = []
    for rca_file in sorted((td / "rca").glob("rca_*.json")):
        try:
            with open(rca_file, "r", encoding="utf-8") as f:
                results.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    results.sort(key=lambda r: (r.get("total_steps", 0), r.get("created_at", "")), reverse=True)
    return results


def load_rca_results_for_agent(agent_dir: str | Path | None = None) -> list[dict]:
    """Load one representative RCA per OSWorld task from all debugger runs.

    The annotation task list should be keyed by the agent trajectory, not by
    the debugger model. If three debugger models produced RCA for the same
    task, this returns one task row and the per-model RCA payloads are loaded
    later by load_debugger_refs().
    """
    if not agent_dir:
        latest = _find_latest_trial()
        agent_dir = _agent_dir(latest) if latest else None
    if not agent_dir:
        return []

    by_task: dict[str, dict] = {}
    for debugger_dir in _debugger_trial_dirs_for_agent(agent_dir):
        for r in load_rca_results(str(debugger_dir)):
            task_id = r.get("task_id")
            if task_id and task_id not in by_task:
                by_task[task_id] = r
    results = list(by_task.values())
    results.sort(key=lambda r: (r.get("total_steps", 0), r.get("created_at", "")), reverse=True)
    if not results:
        return load_annotation_results_for_agent(agent_dir)
    return results


def _confidence_to_float(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return {"high": 0.9, "mid": 0.7, "medium": 0.7, "low": 0.5}.get(value.lower(), 0.0)
    return 0.0


def _task_id_from_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def _app_id_from_path(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return parts[-2] if len(parts) >= 2 else "unknown"


def _classification_lookup(agent_dir: str | Path) -> dict[str, dict]:
    """Map task_id to trajectory metadata from classification.json."""
    path = _agent_dir(agent_dir) / "classification.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    lookup: dict[str, dict] = {}
    for status, paths in data.get("trajectories", {}).items():
        if not isinstance(paths, list):
            continue
        for raw_path in paths:
            if not isinstance(raw_path, str):
                continue
            lookup[_task_id_from_path(raw_path)] = {
                "traj_path": _remap_path(raw_path) or raw_path,
                "app_id": _app_id_from_path(raw_path),
                "status": status,
            }
    return lookup


def _trajectory_preview(traj_path: str | None) -> tuple[int, str]:
    if not traj_path:
        return 0, ""
    traj_dir = Path(traj_path)
    for name in ("traj.jsonl", "trajectory.jsonl"):
        fpath = traj_dir / name
        if not _path_exists(fpath):
            continue
        count = 0
        instruction = ""
        try:
            with open(_windows_long_path(fpath), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if not instruction:
                            instruction = infer_instruction_from_entry(entry)
                        if "step_num" in entry:
                            count += 1
                    except json.JSONDecodeError:
                        continue
            return count, instruction
        except OSError:
            return 0, ""
    return 0, ""


def _count_trajectory_steps(traj_path: str | None) -> int:
    return _trajectory_preview(traj_path)[0]


def load_annotation_results_for_agent(agent_dir: str | Path | None = None) -> list[dict]:
    """Build viewer task rows from human annotations when RCA JSON is absent."""
    if not agent_dir:
        return []

    agent = _agent_dir(agent_dir)
    ann_dir = agent / "annotations"
    if not ann_dir.is_dir():
        return []

    class_by_task = _classification_lookup(agent)
    results: list[dict] = []
    for ann_file in sorted(ann_dir.glob("human_*.json")):
        try:
            with open(ann_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        task_id = payload.get("task_id") or ann_file.stem.removeprefix("human_")
        human_values = payload.get("human_values", [])
        if isinstance(human_values, list):
            first_human = human_values[0] if human_values else {}
        elif isinstance(human_values, dict):
            first_human = human_values
        else:
            first_human = {}
        llm_values = payload.get("llm_values") if isinstance(payload.get("llm_values"), dict) else {}
        values = llm_values or first_human
        if not values:
            continue

        meta = class_by_task.get(task_id, {})
        traj_path = meta.get("traj_path", "")
        root_step = int(values.get("root_error_step") or first_human.get("root_error_step") or 1)
        step_count, inferred_instruction = _trajectory_preview(traj_path)
        total_steps = max(step_count, root_step, 1)
        results.append(
            {
                "task_id": task_id,
                "instruction": payload.get("instruction") or inferred_instruction,
                "traj_path": traj_path,
                "app_id": meta.get("app_id", "unknown"),
                "status": meta.get("status", "annotated"),
                "root_error_step": root_step,
                "taxonomy_tag": values.get("taxonomy_tag") or first_human.get("taxonomy_tag") or "Other",
                "evidence": values.get("evidence") or first_human.get("evidence") or "",
                "correction": values.get("correction") or first_human.get("correction") or "",
                "confidence": _confidence_to_float(values.get("confidence") or first_human.get("confidence")),
                "model": "annotation",
                "total_steps": total_steps,
                "terminal_step": total_steps,
                "created_at": datetime.fromtimestamp(ann_file.stat().st_mtime).isoformat(),
                "steps": [],
            }
        )

    results.sort(key=lambda r: (r.get("app_id", ""), r.get("task_id", "")))
    return results


def load_episodic_memory(trial_dir: str | None = None):
    td = Path(trial_dir) if trial_dir else _find_latest_trial()
    if not td:
        return []

    records: list[dict] = []
    search_dirs = [td] if (td / "episodic.json").exists() else _debugger_trial_dirs_for_agent(td)
    for d in search_dirs:
        ep_file = d / "episodic.json"
        if not ep_file.exists():
            continue
        with open(ep_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        records.extend(list(data.get("records", {}).values()))
    return records


def load_full_trajectory(traj_path: str) -> dict:
    """Load the full trajectory using the debugger's trajectory loader."""
    from debugger.trajectory import load_normalized_trajectory
    traj_dir = Path(traj_path)
    if traj_dir.is_dir():
        return load_normalized_trajectory(traj_dir)
    return {}


def load_a11y_tree(traj_path: str, step_num: int, action_timestamp: str) -> str:
    """Load accessibility tree text for a given step."""
    traj_dir = Path(traj_path)
    a11y_dir = traj_dir / "a11y_trees"
    if not a11y_dir.is_dir():
        return ""
    # Try exact match with timestamp
    if action_timestamp:
        fpath = a11y_dir / f"step_{step_num}_{action_timestamp}.txt"
        if fpath.exists():
            return fpath.read_text(errors="replace")
    # Fallback: glob for step_N_*.txt
    matches = list(a11y_dir.glob(f"step_{step_num}_*.txt"))
    if matches:
        return matches[0].read_text(errors="replace")
    return ""


def load_raw_trajectory(traj_path: str) -> list[dict]:
    """Load raw trajectory JSONL entries (with all original fields)."""
    traj_dir = Path(traj_path)
    for name in ("trajectory.jsonl", "traj.jsonl"):
        fpath = traj_dir / name
        if fpath.exists():
            steps = []
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            steps.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            return steps
    return []


# â”€â”€ Annotation helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _agent_dir(trial_dir: str | Path) -> Path:
    """Return the agent-level dir for a given trial.

    Resolution order:
      1. ``trial_dir`` itself carries an annotation artifact (flat layout, or the
         caller already passed an agent-level path) â†’ return it.
      2. Parent carries one (``trial_dir`` is a debugger subdir under an agent) â†’
         return parent.
      3. ``trial_dir`` has at least one ``<sub>/rca/`` child (agent dir with
         debugger subdirs but no annotation artifacts yet) â†’ return it.
      4. Fall back to parent.

    Annotation artifacts: ``annotations/``, ``annotation_assignments.json``,
    ``classification.json``, ``repeat/``.
    """
    p = Path(trial_dir)

    if _has_annotation_artifact(p):
        return p
    if _has_annotation_artifact(p.parent):
        return p.parent
    if p.is_dir() and any((c / "rca").is_dir() for c in p.iterdir() if c.is_dir()):
        return p
    return p.parent


def load_assignments(trial_dir: str) -> dict | None:
    """Load annotation_assignments.json from the agent-level dir."""
    p = _agent_dir(trial_dir) / "annotation_assignments.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _read_annotation_file(path: Path) -> dict:
    """Read an annotation file, auto-migrating legacy schemas in place.

    Two legacy shapes are normalized:

    1. v1: ``human_values`` is a single dict and the annotator name lives at
       the top level. The dict is wrapped into a single-element list and the
       top-level identifier fields are folded into that entry.
    2. v2-with-legacy-top-level: ``human_values`` is already a list, but the
       file still carries stale top-level ``annotator`` / ``created_at`` /
       ``updated_at`` fields duplicated from the v1 format. Those fields
       belong inside each entry now, so we strip them.

    In either case the file is rewritten on disk so later code paths can
    assume a clean v2 shape.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    hv = payload.get("human_values")
    mutated = False

    if isinstance(hv, dict):
        # v1 â†’ v2: wrap the single dict into a list.
        if any(v not in (None, "") for v in hv.values()):
            entry = {
                "annotator": payload.get("annotator", ""),
                "root_error_step": hv.get("root_error_step"),
                "taxonomy_tag": hv.get("taxonomy_tag"),
                "evidence": hv.get("evidence"),
                "correction": hv.get("correction"),
                "confidence": hv.get("confidence"),
                "updated_at": payload.get("updated_at")
                    or payload.get("created_at")
                    or datetime.now().isoformat(),
            }
            payload["human_values"] = [entry]
        else:
            payload["human_values"] = []
        mutated = True
    elif not isinstance(hv, list):
        payload["human_values"] = []
        mutated = True

    # Strip legacy top-level identifier fields â€” the per-entry copy is
    # authoritative now, and keeping them causes confusing duplication.
    for legacy_key in ("annotator", "created_at", "updated_at"):
        if legacy_key in payload:
            payload.pop(legacy_key, None)
            mutated = True

    if mutated:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
    return payload


def load_annotation(trial_dir: str, task_id: str, annotator_name: str = "") -> dict | None:
    """Load a human annotation file, scoped by annotator when using v2 schema.

    For v2 files (annotations/), human_values is a list of per-annotator
    entries. When *annotator_name* is provided, the returned dict has
    human_values set to that annotator's single entry dict (not a list) for
    backward compat with form pre-fill logic. Also sets payload["annotator"].
    """
    # --- v2 path (list schema) ---
    v2_file = _agent_dir(trial_dir) / "annotations" / f"human_{task_id}.json"
    if v2_file.exists():
        payload = _read_annotation_file(v2_file)
        human_list = payload.get("human_values", [])
        if not isinstance(human_list, list):
            human_list = [human_list] if human_list else []
        if annotator_name:
            for entry in human_list:
                if entry.get("annotator") == annotator_name:
                    payload["human_values"] = entry
                    payload["annotator"] = annotator_name
                    return payload
            # annotator not found in list â€” return payload with empty human_values
            payload["human_values"] = {}
            payload["annotator"] = annotator_name
            return payload
        # No annotator specified â€” return first entry for backward compat
        if human_list:
            payload["human_values"] = human_list[0]
            payload["annotator"] = human_list[0].get("annotator", "")
            return payload
        # human_values list is empty â€” return payload as-is
        payload["human_values"] = {}
        payload["annotator"] = ""
        return payload
    # --- v1 fallback ---
    ann_file = _agent_dir(trial_dir) / "annotations" / f"human_{task_id}.json"
    if ann_file.exists():
        with open(ann_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_annotation(trial_dir: str, task_id: str, task: dict,
                    human_values: dict, annotator: str, notes: str,
                    chosen_debugger: str | None = None) -> Path:
    """Save a human annotation using v2 append-by-annotator semantics.

    Writes to ``annotations/human_<task_id>.json``.  If the file already
    exists, the annotator's entry in the ``human_values`` list is updated
    in-place (or appended if new).  Other annotators' entries are untouched.

    When *chosen_debugger* is provided, it is written into the per-annotator
    entry as the ``chosen_debugger`` key (the model string the annotator picked
    from the multi-debugger UI). When omitted/None, the key is absent from the
    saved entry â€” back-compat by absence per Phase 9 D-03.
    """
    ann_dir = _agent_dir(trial_dir) / "annotations"
    ann_dir.mkdir(exist_ok=True)
    ann_file = ann_dir / f"human_{task_id}.json"

    # Read existing v2 file if present (auto-migrates v1 â†’ v2 on disk)
    existing_data: dict | None = None
    if ann_file.exists():
        existing_data = _read_annotation_file(ann_file)

    llm_values = {
        "root_error_step": task["root_error_step"],
        "taxonomy_tag": task["taxonomy_tag"],
        "evidence": task["evidence"],
        "correction": task["correction"],
        "confidence": task["confidence"],
    }

    # Build the per-annotator entry
    entry = {
        "annotator": annotator,
        "root_error_step": human_values["root_error_step"],
        "taxonomy_tag": human_values["taxonomy_tag"],
        "evidence": human_values["evidence"],
        "correction": human_values["correction"],
        "confidence": human_values["confidence"],
        "updated_at": datetime.now().isoformat(),
    }
    if chosen_debugger is not None:
        entry["chosen_debugger"] = chosen_debugger

    # Merge into existing human_values list or create new
    if existing_data and isinstance(existing_data.get("human_values"), list):
        hv_list = existing_data["human_values"]
    else:
        hv_list = []

    # Update existing entry for this annotator, or append
    found = False
    for i, e in enumerate(hv_list):
        if e.get("annotator") == annotator:
            hv_list[i] = entry
            found = True
            break
    if not found:
        hv_list.append(entry)

    data = {
        "task_id": task_id,
        "trial": _agent_dir(trial_dir).name,  # agent-level name (annotations are agent-scoped)
        "llm_values": llm_values,
        "human_values": hv_list,
        "notes": notes,
    }

    with open(ann_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return ann_file


def check_annotation_exists(trial_dir: str, task_id: str, annotator_name: str = "") -> bool:
    """Check if a human annotation exists. If annotator_name given, check that specific annotator."""
    v2_file = _agent_dir(trial_dir) / "annotations" / f"human_{task_id}.json"
    if v2_file.exists():
        if not annotator_name:
            return True
        data = _read_annotation_file(v2_file)
        return any(
            isinstance(e, dict) and e.get("annotator") == annotator_name
            for e in data.get("human_values", [])
        )
    return False


# â”€â”€ Image helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def parse_click_coordinates(code_str: str):
    """Extract click coordinates from pyautogui action code."""
    if not isinstance(code_str, str):
        return None, None
    m = re.search(r'pyautogui\.click\(\(?\s*(\d+)\s*,\s*(\d+)\s*\)?\)', code_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Match variable assignment like index_4 = (35, 133) followed by click
    m2 = re.search(r'=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', code_str)
    if m2 and 'click' in code_str:
        return int(m2.group(1)), int(m2.group(2))
    # Match x=..., y=... style
    x_match = re.search(r'x\s*=\s*(\d+(?:\.\d+)?)', code_str)
    y_match = re.search(r'y\s*=\s*(\d+(?:\.\d+)?)', code_str)
    if x_match and y_match:
        return float(x_match.group(1)), float(y_match.group(1))
    return None, None


def draw_click_marker(img: Image.Image, x, y) -> Image.Image:
    """Draw a red circle on the image at the click coordinates."""
    img = img.copy()
    img_w, img_h = img.size
    if x <= 1.0 and y <= 1.0:
        x, y = x * img_w, y * img_h
    draw = ImageDraw.Draw(img)
    r = 15
    color = '#ff2b2b'
    draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=5)
    draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
    return img


def step_status_display(step_num: int, root_step: int, has_error: bool) -> tuple[str, str, str]:
    """Return CSS class, status label, and visible heading for a trajectory step."""
    if step_num == root_step:
        css_class, label = "root-step", "ROOT CAUSE"
    elif step_num > root_step and has_error:
        css_class, label = "cascade-step", "CASCADED"
    elif has_error:
        css_class, label = "cascade-step", "ERROR"
    else:
        css_class, label = "normal-step", "OK"
    return css_class, label, f"Step {step_num} - {label}"


# â”€â”€ Discussion panel helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@st.cache_data(ttl=300, show_spinner=False)
def _get_available_models() -> list[dict]:
    """Discover models from each provider's /models endpoint at runtime.

    Anthropic via native SDK; everything else via OpenAI-compat (one entry per
    configured base_urls[<provider>], plus default openai endpoint). Skipped
    silently when the key env is unset or the endpoint is unreachable.
    """
    out: list[dict] = []

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            for m in Anthropic().models.list().data:
                out.append({"provider": "anthropic", "model": m.id, "label": m.id})
        except Exception:
            pass

    bases = dict(_cfg.base_urls)
    bases.setdefault("openai", "https://api.openai.com/v1")
    for prov, url in bases.items():
        key = os.environ.get(f"{prov.upper()}_API_KEY")
        if not key:
            continue
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=url, timeout=10, max_retries=0)
            for m in client.models.list().data:
                out.append({"provider": prov, "model": m.id, "label": m.id})
        except Exception:
            pass

    return out


def _provider_env_keys(provider: str) -> list[str]:
    """Required env var names for a provider â€” always <PROVIDER>_API_KEY."""
    return [f"{provider.upper()}_API_KEY"]


def _make_llm_client(provider: str, model: str):
    """Create an LLM client for the given provider and model.

    Raises ValueError with a helpful message if required env vars are missing.
    """
    missing = [k for k in _provider_env_keys(provider) if not os.environ.get(k)]
    if missing:
        raise ValueError(
            f"Missing env vars for {provider}: {', '.join(missing)}. "
            f"Export them and restart Streamlit."
        )
    if provider == "anthropic":
        from anthropic import Anthropic
        return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    elif provider == "together":
        from debugger.together_adapter import TogetherAnthropicAdapter
        return TogetherAnthropicAdapter(model=model, api_key=os.environ["TOGETHER_API_KEY"])
    # OpenAI-compatible (openai, gemini, azure, openrouter, ...) â€” picks base_urls[provider]
    # and <PROVIDER>_API_KEY (e.g. GEMINI_API_KEY for provider=gemini).
    from debugger.openai_adapter import OpenAICompatAdapter
    key_env = f"{provider.upper()}_API_KEY"
    base_url = _cfg.base_urls.get(provider)
    if provider == "openai" and not base_url:
        base_url = "https://api.openai.com/v1"
    if not base_url:
        raise ValueError(
            f"provider='{provider}' needs a base_url. "
            f"Add base_urls['{provider}'] in debugger/config/debugger.json."
        )
    return OpenAICompatAdapter(
        model=model,
        api_key=os.environ[key_env],
        base_url=base_url,
    )


def _get_llm_client():
    """Return the currently selected LLM client (from session state)."""
    provider = st.session_state.get("_llm_provider")
    model = st.session_state.get("_llm_model")
    if not provider or not model:
        # Fallback to config defaults
        provider = _cfg.provider
        model = _cfg.model
    cached_key = st.session_state.get("_llm_cache_key")
    target_key = f"{provider}:{model}"
    if cached_key != target_key:
        try:
            client = _make_llm_client(provider, model)
        except ValueError as e:
            st.error(str(e))
            return None
        if client is None:
            return None
        st.session_state["_llm_client"] = client
        st.session_state["_llm_model"] = model
        st.session_state["_llm_provider"] = provider
        st.session_state["_llm_cache_key"] = target_key
    return st.session_state.get("_llm_client")


def _load_discussion_log(trial_dir: str, task_id: str) -> dict | None:
    """Load a saved discussion log if it exists."""
    log_file = Path(trial_dir) / "discussions" / f"discuss_{task_id}.json"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _delete_discussion_log(trial_dir: str, task_id: str):
    """Delete a saved discussion log file."""
    log_file = Path(trial_dir) / "discussions" / f"discuss_{task_id}.json"
    if log_file.exists():
        log_file.unlink()


def _save_discussion_log(trial_dir: str, task_id: str, chat_history: list[dict],
                         proposal: dict | None = None):
    """Persist the discussion conversation log to the trial's discussions folder."""
    disc_dir = Path(trial_dir) / "discussions"
    disc_dir.mkdir(exist_ok=True)
    log_file = disc_dir / f"discuss_{task_id}.json"

    data = {
        "task_id": task_id,
        "updated_at": datetime.now().isoformat(),
        "messages": chat_history,
    }
    if proposal:
        data["last_proposal"] = proposal

    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _render_discussion_panel(task: dict, traj_path: str, trial_dir: str):
    """Render the interactive discussion chat panel."""
    from debugger.discuss import DiscussionSession

    # â”€â”€ Model selector â”€â”€
    available = _get_available_models()
    if available:
        # Determine default: the model that ran this RCA
        rca_model = task.get("model", "")
        default_label = available[0]["label"]
        label_to_entry = {}
        for m in available:
            label_to_entry[m["label"]] = m
            if m["model"] == rca_model:
                default_label = m["label"]

        all_labels = [m["label"] for m in available]
        default_index = all_labels.index(default_label) if default_label in all_labels else 0

        selected_label = st.selectbox(
            "Discussion model",
            all_labels,
            index=default_index,
            key="_discuss_model_selector",
        )
        chosen = label_to_entry[selected_label]

        # Detect model switch â€” reset session but keep chat history visible
        prev_key = st.session_state.get("_discuss_active_model_key")
        new_key = f"{chosen['provider']}:{chosen['model']}"
        if prev_key and prev_key != new_key:
            # Model changed: discard the DiscussionSession so it gets recreated
            st.session_state.pop("_discuss_session", None)

        st.session_state["_discuss_active_model_key"] = new_key
        st.session_state["_llm_provider"] = chosen["provider"]
        st.session_state["_llm_model"] = chosen["model"]
    else:
        st.caption("No LLM API keys configured.")

    # Display chat history
    chat_history: list[dict] = st.session_state.get("_discuss_history", [])

    # Header controls: message count + clear button
    if chat_history:
        ctrl_left, ctrl_right = st.columns([3, 1])
        with ctrl_left:
            st.caption(f"{len(chat_history)} messages")
        with ctrl_right:
            if st.button("Clear", key="_clear_discuss", use_container_width=True):
                for k in list(st.session_state.keys()):
                    if k.startswith("_discuss"):
                        del st.session_state[k]
                if trial_dir:
                    _delete_discussion_log(trial_dir, task["task_id"])
                st.rerun()

    for msg in chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Display pending proposal if any
    proposal = st.session_state.get("_discuss_proposal")
    if proposal:
        st.info("**Proposed Annotation Changes:**")
        pcol1, pcol2 = st.columns(2)
        with pcol1:
            st.markdown(f"**Root Error Step:** {proposal.get('root_error_step', '?')}")
            st.markdown(f"**Taxonomy Tag:** {proposal.get('taxonomy_tag', '?')}")
            st.markdown(f"**Confidence:** {proposal.get('confidence', '?')}")
        with pcol2:
            st.markdown(f"**Evidence:** {proposal.get('evidence', '?')[:200]}...")
            st.markdown(f"**Correction:** {proposal.get('correction', '?')[:200]}...")
        if proposal.get("reasoning"):
            st.caption(f"Reasoning: {proposal['reasoning']}")

        if st.button("Apply to Annotation", type="primary", key="_apply_proposal"):
            _apply_proposal(proposal)
            st.session_state["_discuss_proposal"] = None
            st.rerun()

    # Chat input
    user_input = st.chat_input(
        "Ask the debugger about its analysis...",
        key="_discuss_input",
    )

    if user_input:
        # Initialize session lazily on first message
        client = _get_llm_client()
        if client is None:
            st.error("No LLM client configured. Check debugger config and API keys.")
            return

        session: DiscussionSession | None = st.session_state.get("_discuss_session")
        if session is None:
            session = DiscussionSession(
                client=client,
                model=st.session_state.get("_llm_model", _cfg.model),
                task_rca=task,
                traj_path=traj_path or "",
            )
            st.session_state["_discuss_session"] = session

        # Show user message immediately
        chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Stream agent response
        with st.chat_message("assistant"):
            if session.supports_stream:
                stream_gen = session.send_message_stream(user_input)
                response_text = st.write_stream(stream_gen)
                _, new_proposal = session.get_stream_result()
            else:
                with st.spinner("Debugger is thinking..."):
                    response_text, new_proposal = session.send_message(user_input)
                st.markdown(response_text)

        chat_history.append({"role": "assistant", "content": response_text})
        st.session_state["_discuss_history"] = chat_history

        if new_proposal:
            st.session_state["_discuss_proposal"] = new_proposal

        # Persist conversation log
        if trial_dir:
            _save_discussion_log(
                trial_dir, task["task_id"], chat_history, new_proposal,
            )

        st.rerun()


def _apply_proposal(proposal: dict):
    """Write proposed annotation values into the human annotation session state."""
    st.session_state["_used_annotation_from_discuss_with_debugger"] = True
    if "root_error_step" in proposal:
        st.session_state["human_root_step"] = proposal["root_error_step"]
    if "taxonomy_tag" in proposal:
        tag = proposal["taxonomy_tag"]
        tag_list = ALL_SUBTYPES + ["Other"]
        if tag in ALL_SUBTYPES:
            st.session_state["human_taxonomy_tag"] = tag_list.index(tag)
        else:
            st.session_state["human_taxonomy_tag"] = len(ALL_SUBTYPES)  # "Other"
            st.session_state["human_custom_tag"] = tag
    if "confidence" in proposal:
        st.session_state["human_confidence"] = proposal["confidence"]
    if "evidence" in proposal:
        st.session_state["human_evidence"] = proposal["evidence"]
    if "correction" in proposal:
        st.session_state["human_correction"] = proposal["correction"]


# â”€â”€ Accuracy Dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_accuracy_dashboard(trial_dir: str):
    """Render the accuracy evaluation dashboard for a trial."""
    import pandas as pd

    trial_path = Path(trial_dir)
    report = compute_accuracy(trial_path)

    # â”€â”€ Header metrics â”€â”€
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trial", report["trial"])
    c2.metric("Debugger Model", report["debugger_model"])
    c3.metric("Agent Model", report["agent_model"])
    c4.metric("Sample Size", report["sample_size"])

    if report["skipped_no_human"] > 0:
        st.caption(f"Skipped {report['skipped_no_human']} tasks without human annotations.")

    if report["sample_size"] == 0:
        st.warning("No paired RCA + human annotation data found for this trial.")
        return

    st.markdown("---")

    # â”€â”€ Overall Accuracy â”€â”€
    st.markdown("### Overall Accuracy")
    overall = report["overall"]
    oc1, oc2, oc3, oc4, oc5 = st.columns(5)
    oc1.metric("Tag Exact", f"{overall['tag_exact_accuracy']:.1%}")
    oc2.metric("Tag Category", f"{overall['tag_category_accuracy']:.1%}")
    oc3.metric("Step Exact", f"{overall['step_exact_accuracy']:.1%}")
    oc4.metric("Step +/-2", f"{overall['step_within_2_accuracy']:.1%}")
    oc5.metric("Fully Correct", f"{overall['fully_correct']:.1%}")

    st.markdown("---")

    # â”€â”€ Per-Category Breakdown â”€â”€
    st.markdown("### Per-Category Breakdown")
    cat_data = report["taxonomy_tag"]["per_category"]
    if cat_data:
        rows = []
        for cat in sorted(cat_data.keys()):
            m = cat_data[cat]
            rows.append({
                "Category": cat,
                "Precision": f"{m['precision']:.1%}",
                "Recall": f"{m['recall']:.1%}",
                "F1": f"{m['f1']:.1%}",
                "Support": m["support"],
            })
        st.dataframe(pd.DataFrame(rows).set_index("Category"), use_container_width=True)

    # â”€â”€ Per-Sub-Type Breakdown â”€â”€
    sub_data = report["taxonomy_tag"]["per_sub_type"]
    if sub_data:
        with st.expander(f"Per-Sub-Type Breakdown ({len(sub_data)} types)"):
            rows = []
            for tag in sorted(sub_data.keys()):
                m = sub_data[tag]
                rows.append({
                    "Tag": tag,
                    "Precision": f"{m['precision']:.1%}",
                    "Recall": f"{m['recall']:.1%}",
                    "F1": f"{m['f1']:.1%}",
                    "Support": m["support"],
                })
            st.dataframe(pd.DataFrame(rows).set_index("Tag"), use_container_width=True)

    # â”€â”€ Confusion Matrix â”€â”€
    cm = report["taxonomy_tag"]["confusion_matrix"]
    if cm["labels"]:
        with st.expander("Confusion Matrix (rows=human, cols=LLM)"):
            labels = cm["labels"]
            df_cm = pd.DataFrame(cm["matrix"], index=labels, columns=labels)
            st.dataframe(df_cm, use_container_width=True)

    st.markdown("---")

    # â”€â”€ Root Error Step Analysis â”€â”€
    st.markdown("### Root Error Step Analysis")
    step = report["root_error_step"]
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Exact Match", f"{step['exact_match']:.1%}")
    sc2.metric("MAE", f"{step['mae']:.2f} steps")
    sc3.metric("Mean Signed Error", f"{step['mean_signed_error']:+.2f}")
    sc4.metric("Bias", step["bias_direction"].capitalize())

    tc1, tc2, tc3 = st.columns(3)
    tc1.metric("Within +/-1", f"{step['within_1']:.1%}")
    tc2.metric("Within +/-2", f"{step['within_2']:.1%}")
    tc3.metric("Within +/-3", f"{step['within_3']:.1%}")

    dc1, dc2, dc3 = st.columns(3)
    dc1.metric("Early (LLM < Human)", step["count_early"])
    dc2.metric("Exact", step["count_exact"])
    dc3.metric("Late (LLM > Human)", step["count_late"])

    st.markdown("---")

    # â”€â”€ Per-Annotator Breakdown â”€â”€
    ann_data = report["per_annotator"]
    if ann_data:
        with st.expander(f"Per-Annotator Breakdown ({len(ann_data)} annotators)"):
            rows = []
            for name in sorted(ann_data.keys()):
                m = ann_data[name]
                rows.append({
                    "Annotator": name,
                    "Count": m["count"],
                    "Tag Exact": f"{m['tag_exact_accuracy']:.1%}",
                    "Tag Category": f"{m['tag_category_accuracy']:.1%}",
                    "Step Exact": f"{m['step_exact_accuracy']:.1%}",
                    "Fully Correct": f"{m['fully_correct']:.1%}",
                })
            st.dataframe(pd.DataFrame(rows).set_index("Annotator"), use_container_width=True)

    # â”€â”€ Save / Load â”€â”€
    st.markdown("---")
    save_col, load_col = st.columns(2)
    ANALYSIS_DIR.mkdir(exist_ok=True)
    save_path = ANALYSIS_DIR / f"{report['trial']}_accuracy.json"

    with save_col:
        if st.button("Save Report", use_container_width=True):
            save_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            st.success(f"Saved to `{save_path.relative_to(PROJECT_ROOT)}`")

    with load_col:
        if save_path.exists():
            mtime = datetime.fromtimestamp(save_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            st.caption(f"Saved report exists ({mtime})")
        else:
            st.caption("No saved report yet.")


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    st.sidebar.title("Debugger Vis")

    # â”€â”€ Mode selector â”€â”€
    mode = st.sidebar.radio("Mode", ["RCA Viewer", "Accuracy"], horizontal=True)

    # â”€â”€ Trial selection â”€â”€
    # RCA Viewer works at the agent-trial level:
    #   nested:  <output>/<agent>/<debugger>/rca/ -> select <agent>
    #   flat:    <output>/<trial>/rca/            -> select <trial>
    # Accuracy still works at debugger-run level because compute_accuracy()
    # compares one debugger model's RCA files against shared annotations.
    trial_dir = None
    compare_trial_dirs: list[str] = []
    if mode == "Accuracy":
        trial_dir = _select_trial("Debugger Trial", _discover_debugger_trial_dirs())
    else:
        trial_dir = _select_trial("Agent Trial", _discover_agent_trial_dirs())
        if trial_dir:
            debugger_dirs = _debugger_trial_dirs_for_agent(trial_dir)
            compare_trial_dirs = [str(p) for p in debugger_dirs]
            if debugger_dirs:
                model_labels = [p.name for p in debugger_dirs]
                st.sidebar.caption(
                    "Debugger refs: " + ", ".join(f"`{m}`" for m in model_labels)
                )

    # â”€â”€ Accuracy mode â”€â”€
    if mode == "Accuracy":
        if not trial_dir:
            st.warning("No trial data found. Run the debugger pipeline first.")
            st.stop()
        _render_accuracy_dashboard(trial_dir)
        return

    # â”€â”€ RCA Viewer mode (everything below) â”€â”€
    results = load_rca_results_for_agent(trial_dir)
    memory_records = load_episodic_memory(trial_dir)

    if not results:
        st.warning("New log: No RCA results found. Run the debugger pipeline first:\n\n"
                   "`python -m debugger`")

    # â”€â”€ Sidebar: task selection â”€â”€
    st.sidebar.subheader("Task Selection")

    all_tags = sorted(set(r["taxonomy_tag"] for r in results))
    sel_tags = st.sidebar.multiselect("Taxonomy Tag", all_tags, default=all_tags)

    all_apps = sorted(set(r.get("app_id", "unknown") for r in results))
    sel_apps = st.sidebar.multiselect("App", all_apps, default=all_apps)

    search_query = st.sidebar.text_input("Search by task ID", placeholder="e.g. 7b6c7e24")

    # â”€â”€ Annotator assignment filter (optional) â”€â”€
    assignments = load_assignments(trial_dir) if trial_dir else None
    # Build per-annotator per-round task sets
    _annotator_round_tasks: dict[str, dict[str, set]] = {}  # name -> {"round_1": set, "round_2": set}
    _has_rounds = False
    if assignments:
        for name, val in assignments.get("assignments", {}).items():
            if isinstance(val, dict):
                _has_rounds = True
                _annotator_round_tasks[name] = {
                    "round_1": set(val.get("round_1", [])),
                    "round_2": set(val.get("round_2", [])),
                }
            else:
                _annotator_round_tasks[name] = {"round_1": set(val)}

    sel_annotator = None
    sel_round = "All"
    if _annotator_round_tasks:
        annotator_names = ["All"] + assignments.get("annotators", sorted(_annotator_round_tasks.keys()))
        sel_annotator = st.sidebar.selectbox("Assigned To", annotator_names)
        if _has_rounds:
            sel_round = st.sidebar.radio("Round", ["All", "Round 1", "Round 2"], horizontal=True)

    # Build the set of task IDs matching current annotator + round selection
    def _get_assigned_tasks() -> set[str] | None:
        """Return set of task IDs for current filter, or None for no filtering."""
        if not sel_annotator or sel_annotator == "All":
            if sel_round == "All":
                return None  # no filtering
            # All annotators, specific round
            round_key = "round_1" if sel_round == "Round 1" else "round_2"
            tasks = set()
            for rounds in _annotator_round_tasks.values():
                tasks.update(rounds.get(round_key, set()))
            return tasks
        # Specific annotator
        rounds = _annotator_round_tasks.get(sel_annotator, {})
        if sel_round == "Round 1":
            return rounds.get("round_1", set())
        elif sel_round == "Round 2":
            return rounds.get("round_2", set())
        else:
            return rounds.get("round_1", set()) | rounds.get("round_2", set())

    _assigned_tasks = _get_assigned_tasks()

    filtered = [r for r in results if r["taxonomy_tag"] in sel_tags and r.get("app_id", "unknown") in sel_apps]
    if _assigned_tasks is not None:
        filtered = [r for r in filtered if r["task_id"] in _assigned_tasks]
    if search_query:
        q = search_query.strip().lower()
        filtered = [r for r in filtered if q in r.get("task_id", "").lower()]

    if not filtered:
        st.warning("No results match filters.")
        st.stop()

    # Build annotation status lookup for filtered tasks (scoped by annotator)
    _annotated_set: set[str] = set()
    if trial_dir:
        _filter_annotator = sel_annotator if sel_annotator and sel_annotator != "All" else ""
        for r in filtered:
            if check_annotation_exists(trial_dir, r["task_id"], _filter_annotator):
                _annotated_set.add(r["task_id"])

    # Annotation status filter
    ann_filter = st.sidebar.radio(
        "Annotation Status", ["All", "Not Annotated", "Annotated"],
        horizontal=True,
    )
    if ann_filter == "Not Annotated":
        filtered = [r for r in filtered if r["task_id"] not in _annotated_set]
    elif ann_filter == "Annotated":
        filtered = [r for r in filtered if r["task_id"] in _annotated_set]

    if not filtered:
        st.warning("No results match filters.")
        st.stop()

    st.sidebar.metric("Tasks", len(filtered), delta=f"Total: {len(results)}", delta_color="off")

    if trial_dir:
        annotated_count = sum(1 for r in filtered if r["task_id"] in _annotated_set)
        st.sidebar.metric(
            "Annotated",
            f"{annotated_count}/{len(filtered)}",
            delta=f"{annotated_count / len(filtered) * 100:.0f}%" if filtered else "0%",
            delta_color="normal" if annotated_count < len(filtered) else "off",
        )

    # Detect duplicate task_ids to show distinguishing info
    _task_id_counts: dict[str, int] = {}
    for r in filtered:
        _task_id_counts[r["task_id"]] = _task_id_counts.get(r["task_id"], 0) + 1

    def fmt_task(i):
        r = filtered[i]
        is_done = r["task_id"] in _annotated_set
        prefix = "\u2705 " if is_done else "\u2b1c "
        instr = r.get("instruction", "")
        instr_short = (instr[:40] + "...") if len(instr) > 40 else instr
        steps_info = f"{r.get('total_steps', '?')}st"
        # Show source hint when there are duplicates for this task_id
        suffix = f"({r['confidence']:.0%})"
        if _task_id_counts.get(r["task_id"], 1) > 1:
            traj_path = r.get("traj_path", "")
            # Extract a short source name from the traj_path
            parts = Path(traj_path).parts
            source = ""
            for p in parts:
                if "GLM" in p or "Qwen" in p or "verified" in p or "osworld" in p:
                    source = p
                    break
            suffix = f"({steps_info}, {source or '?'}, {r['confidence']:.0%})"
        else:
            suffix = f"({steps_info}, {r['confidence']:.0%})"
        if instr_short:
            return f"{prefix}[{r['taxonomy_tag']}] {instr_short} {suffix}"
        return f"{prefix}[{r['taxonomy_tag']}] {r['task_id'][:12]}... {suffix}"

    # Navigation â€” callbacks run *before* widgets instantiate on the next rerun,
    # so they can safely write to the selectbox key.  We keep a separate integer
    # tracker (_task_idx) because format_func makes the selectbox store the
    # formatted label string, not the raw integer.
    def _nav(delta):
        idx = st.session_state.get("_task_idx", 0) + delta
        st.session_state["_task_idx"] = idx
        st.session_state["task_selector"] = idx

    # Build list of formatted labels as the options (avoids format_func issues)
    task_labels = [fmt_task(i) for i in range(len(filtered))]
    current_nav = st.session_state.get("_task_idx", 0)
    if current_nav < 0 or current_nav >= len(task_labels):
        current_nav = 0
        st.session_state["_task_idx"] = 0

    sel_label = st.sidebar.selectbox(
        "Select Task", task_labels, index=current_nav, key="task_selector",
    )
    sel_idx = task_labels.index(sel_label)
    st.session_state["_task_idx"] = sel_idx  # sync when user picks via dropdown

    task = filtered[sel_idx]

    # Reset annotation form + discussion state when switching tasks (keep annotator_name)
    current_task_id = task["task_id"]
    current_task_key = f"{trial_dir or ''}:{current_task_id}"
    if st.session_state.get("_last_task_id") != current_task_key:
        for k in list(st.session_state.keys()):
            if k.startswith("human_") or k.startswith("llm_") or k.startswith("_discuss") or k == "_used_annotation_from_discuss_with_debugger":
                del st.session_state[k]
        # Reset LLM selection so new task defaults to its own RCA model
        for k in ("_llm_provider", "_llm_model", "_llm_cache_key", "_llm_client"):
            st.session_state.pop(k, None)
        # Phase 9 picker state â€” reset on task switch.
        # IMPORTANT (BLOCK 1 from iteration-1 review): we MUST pop the radio
        # widget's own session_state key (`_picker_choice_widget`), not just
        # our internal `_picker_choice` tracker. Streamlit retains widget
        # session_state across reruns and across task switches; if we leave
        # `_picker_choice_widget` set to e.g. "debugger_0", the radio renders
        # at that index on the NEW task and the pick-change handler fires
        # against the new task's first ref â€” clobbering the human annotation
        # just loaded by load_annotation() below. Popping all three keys
        # ensures the radio truly resets to "None of these" default.
        for k in ("_picker_choice", "_picker_choice_widget", "_chosen_debugger_model"):
            st.session_state.pop(k, None)
        # Restore saved discussion history for this task
        if trial_dir:
            saved_disc = _load_discussion_log(trial_dir, current_task_id)
            if saved_disc:
                st.session_state["_discuss_history"] = saved_disc.get("messages", [])
                if saved_disc.get("last_proposal"):
                    st.session_state["_discuss_proposal"] = saved_disc["last_proposal"]
        # Pre-seed from existing annotation or RCA defaults
        _current_annotator = st.session_state.get("annotator_name", "").strip()
        ann = load_annotation(trial_dir, task["task_id"], _current_annotator) if trial_dir else None
        if ann and ann.get("human_values") and ann["human_values"].get("root_error_step") is not None:
            hv = ann["human_values"]
            st.session_state["human_root_step"] = hv["root_error_step"]
            tag = hv["taxonomy_tag"]
            tag_list = ALL_SUBTYPES + ["Other"]
            st.session_state["human_taxonomy_tag"] = tag_list.index(tag) if tag in ALL_SUBTYPES else len(ALL_SUBTYPES)
            if tag not in ALL_SUBTYPES:
                st.session_state["human_custom_tag"] = tag
            st.session_state["human_confidence"] = hv["confidence"]
            st.session_state["human_evidence"] = hv["evidence"]
            st.session_state["human_correction"] = hv["correction"]
            st.session_state["human_notes"] = ann.get("notes", "")
            st.session_state["_used_annotation_from_discuss_with_debugger"] = ann.get("used_discussion", False)
        else:
            st.session_state["human_root_step"] = task["root_error_step"]
            tag = task["taxonomy_tag"]
            tag_list = ALL_SUBTYPES + ["Other"]
            st.session_state["human_taxonomy_tag"] = tag_list.index(tag) if tag in ALL_SUBTYPES else len(ALL_SUBTYPES)
            st.session_state["human_confidence"] = (
                "high" if task["confidence"] >= 0.9
                else ("mid" if task["confidence"] >= 0.7 else "low")
            )
            st.session_state["human_evidence"] = task["evidence"]
            st.session_state["human_correction"] = task["correction"]
            st.session_state["human_notes"] = ""
        # Phase 9: load multi-debugger refs for this task (uses the trial-dir
        # list built in main() â€” `compare_trial_dirs` is in this function's scope).
        st.session_state["_debugger_refs"] = load_debugger_refs(
            compare_trial_dirs, task["task_id"]
        ) if compare_trial_dirs else []

        # BLOCK 3 (option A) â€” rehydrate picker state from a saved annotation.
        # If the annotator returns to a task they've previously saved with a
        # debugger pick recorded, restore `_chosen_debugger_model` so re-save
        # preserves the field, AND map back to the picker option index so the
        # radio defaults to that option (visual consistency with the saved file).
        # Without this, _chosen_debugger_model would stay None after task switch
        # and a re-save (with no fresh pick) would silently strip the field.
        if ann and isinstance(ann.get("human_values"), dict):
            saved_chosen = ann["human_values"].get("chosen_debugger")
            if saved_chosen:
                st.session_state["_chosen_debugger_model"] = saved_chosen
                # Try to map back to a picker option index for radio default
                for i, ref in enumerate(st.session_state.get("_debugger_refs", [])):
                    if ref.get("model") == saved_chosen:
                        st.session_state["_picker_choice"] = f"debugger_{i}"
                        break
        st.session_state["_last_task_id"] = current_task_key

    nav_prev, nav_next = st.sidebar.columns(2)
    with nav_prev:
        st.button("Prev", disabled=(sel_idx == 0), use_container_width=True,
                  on_click=_nav, args=(-1,))
    with nav_next:
        st.button("Next", disabled=(sel_idx == len(filtered) - 1), use_container_width=True,
                  on_click=_nav, args=(1,))

    # â”€â”€ Discussion Panel Controls â”€â”€
    st.sidebar.markdown("---")
    show_discuss = st.sidebar.toggle(
        "Discussion Panel", value=False, key="_show_discuss",
    )

    # â”€â”€ Resolve trajectory path once (used everywhere below) â”€â”€
    traj_path = _remap_path(task.get("traj_path", ""))

    # â”€â”€ Layout: draggable discussion panel â”€â”€
    if show_discuss:
        main_col, discuss_col = adjustable_columns(
            [3, 1],
            gap="large",
            labels=["Main Content", "Discussion"],
            key="_discuss_resize",
        )
        with discuss_col:
            st.markdown(
                '<div class="discuss-header">'
                '<span class="discuss-icon">Chat</span> Discuss with Debugger'
                '</div>',
                unsafe_allow_html=True,
            )
            _render_discussion_panel(task, traj_path, trial_dir)
        main_ctx = main_col
    else:
        main_ctx = st.container()

    # â”€â”€ Main Content â”€â”€
    with main_ctx:

        # â”€â”€ RCA Summary Card â”€â”€
        st.markdown(f"## Task: `{task['task_id']}`")
        st.markdown(
            f'<div class="instruction-box"><strong>Instruction:</strong> {task.get("instruction", "N/A")}</div>',
            unsafe_allow_html=True,
        )
        if traj_path:
            rel = os.path.relpath(traj_path, str(PROJECT_ROOT)) if os.path.isabs(traj_path) else traj_path
            src_col, btn_col = st.columns([4, 1])
            with src_col:
                st.caption(f"Source: `{rel}`")
            with btn_col:
                abs_path = os.path.abspath(traj_path)
                st.link_button("Open Trajectory", f"vscode://file{abs_path}", use_container_width=True)

        # Load annotation for current task (scoped by annotator)
        _current_annotator = st.session_state.get("annotator_name", "").strip()
        existing_annotation = load_annotation(trial_dir, task["task_id"], _current_annotator) if trial_dir else None
        # Treat empty human_values as no annotation for UI display purposes
        if existing_annotation and not existing_annotation.get("human_values", {}).get("root_error_step"):
            existing_annotation = None

        # Badges
        conf = task["confidence"]
        conf_class = "tag-ok" if conf >= 0.9 else ("tag-warn" if conf >= 0.7 else "tag-error")
        badges_html = f"""
            <span class="tag tag-error">{task['taxonomy_tag']}</span>
            <span class="tag tag-info">{task.get('app_id', '?')}</span>
            <span class="tag {conf_class}">Confidence: {conf:.0%}</span>
            <span class="tag tag-purple">Root Step: {task['root_error_step']}</span>
            <span class="tag tag-info">Model: {task.get('model', '?')}</span>
            <span class="tag tag-info">Steps: {task['total_steps']}</span>
        """
        if existing_annotation:
            badges_html += '    <span class="tag tag-ok">HUMAN REVIEWED</span>\n'
        st.markdown(badges_html, unsafe_allow_html=True)

        st.markdown("")

        # â”€â”€ Phase 9: multi-debugger picker â”€â”€
        refs: list[dict] = st.session_state.get("_debugger_refs", [])
        if not refs:
            # Fallback: no multi-trial refs available â€” show the single Evidence/Correction
            # card from the active task (matches pre-Phase-9 behavior).
            col_ev, col_fix = st.columns(2)
            with col_ev:
                st.markdown("#### Evidence")
                st.error(task["evidence"])
            with col_fix:
                st.markdown("#### Correction")
                st.success(task["correction"])
        else:
            st.markdown("#### Which is better?")
            if len(refs) == 1:
                st.caption(f"Single debugger reference: `{refs[0].get('model', 'unknown')}` "
                           f"(add more trial dirs in the sidebar to enable picking).")

            # Render N cards side-by-side
            ref_cols = st.columns(len(refs))
            for i, (rcol, ref) in enumerate(zip(ref_cols, refs)):
                with rcol:
                    st.markdown(f"**Debugger {chr(ord('A') + i)} â€” `{ref.get('model', 'unknown')}`**")
                    st.markdown(f"Root step: **{ref.get('root_error_step', '?')}** &nbsp;&nbsp; "
                                f"Tag: **{ref.get('taxonomy_tag', '?')}**")
                    st.markdown("*Evidence:*")
                    st.error(ref.get("evidence", ""))
                    st.markdown("*Correction:*")
                    st.success(ref.get("correction", ""))

            # Radio picker â€” only meaningful when N â‰¥ 2; for N == 1 we still
            # render it so the form pre-fill flow stays uniform.
            option_keys = [f"debugger_{i}" for i in range(len(refs))] + ["none"]
            option_labels = [f"Debugger {chr(ord('A') + i)}" for i in range(len(refs))] + ["None of these / write from scratch"]

            # Determine current default index. Priority order:
            #   1. _picker_choice in session_state (set either by previous interaction
            #      OR by the BLOCK 3 rehydration block on task switch) â†’ keep it.
            #   2. fall back to "none" (last option) for fresh tasks.
            current_choice = st.session_state.get("_picker_choice")
            default_idx = option_keys.index(current_choice) if current_choice in option_keys else len(option_keys) - 1  # default = "none"

            picked = st.radio(
                "Which debugger's framing is closest to correct?",
                options=option_keys,
                format_func=lambda k: option_labels[option_keys.index(k)],
                index=default_idx,
                horizontal=True,
                key="_picker_choice_widget",
            )

            # â”€â”€ BLOCK 2 from iteration-1 review: guard pre-fill against
            # clobbering saved annotations. CD-02's contract is "overwrite
            # UNSAVED edits", not saved ones. Use `existing_annotation`
            # (already computed unconditionally above, and already filtered
            # to None when human_values is empty/missing root_error_step).
            # We MUST NOT use `ann` here: `ann` is bound only inside the
            # task-switch branch, so on reruns where the task didn't change,
            # referencing `ann` at this picker render site raises NameError.
            # `existing_annotation` is the correct, always-bound variable.
            existing = existing_annotation

            # Detect pick change and apply pre-fill (CD-02 â€” guarded).
            if picked != current_choice:
                st.session_state["_picker_choice"] = picked
                if picked == "none":
                    st.session_state["_chosen_debugger_model"] = None
                else:
                    idx = int(picked.split("_")[1])
                    chosen = refs[idx]
                    # ALWAYS record the pick for save-time (so re-saves preserve it),
                    # even when the form is locked from a saved annotation.
                    st.session_state["_chosen_debugger_model"] = chosen.get("model")

                    if existing is None:
                        # Fresh task or unsaved edits â€” pre-fill the form (CD-02).
                        st.session_state["human_root_step"] = int(chosen.get("root_error_step", task["root_error_step"]))
                        tag_val = str(chosen.get("taxonomy_tag", task["taxonomy_tag"]))
                        tag_list = ALL_SUBTYPES + ["Other"]
                        st.session_state["human_taxonomy_tag"] = tag_list.index(tag_val) if tag_val in ALL_SUBTYPES else len(ALL_SUBTYPES)
                        if tag_val not in ALL_SUBTYPES:
                            st.session_state["human_custom_tag"] = tag_val
                        st.session_state["human_evidence"] = str(chosen.get("evidence", ""))
                        st.session_state["human_correction"] = str(chosen.get("correction", ""))
                        st.rerun()
                    else:
                        # Saved annotation present â€” DO NOT overwrite form fields.
                        # Just record the pick for save-time and surface a banner.
                        st.info(
                            "This task already has your saved annotation. Pick "
                            "recorded for future reference; form fields not changed."
                        )
                        # No st.rerun() here â€” banner shows on the natural next render.

        # â”€â”€ Human Annotation Form â”€â”€
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        ann_label = "Human Annotation (REVIEWED)" if existing_annotation else "Human Annotation"
        with st.expander(ann_label, expanded=(existing_annotation is not None)):

            # Sync annotator name with "Assigned To" selection
            if sel_annotator and sel_annotator != "All":
                if st.session_state.get("_last_annotator") != sel_annotator:
                    st.session_state["annotator_name"] = sel_annotator
                    st.session_state["_last_annotator"] = sel_annotator
            elif existing_annotation and not st.session_state.get("annotator_name"):
                st.session_state["annotator_name"] = existing_annotation.get("annotator", "")

            annotator_name = st.text_input(
                "Annotator name",
                placeholder="Your name",
                key="annotator_name",
            )

            # Side-by-side: LLM values (read-only) | Human editable
            llm_col, human_col = st.columns(2)

            with llm_col:
                st.markdown("#### LLM Values (read-only)")
                st.markdown(f"**Root Error Step:** {task['root_error_step']}")
                st.markdown(f"**Taxonomy Tag:** {task['taxonomy_tag']} \u2014 "
                            f"{SUBTYPE_DEFINITIONS.get(task['taxonomy_tag'], '')}")
                st.markdown(f"**Confidence:** {task['confidence']:.0%}")
                st.text_area("LLM Evidence", value=task["evidence"], disabled=True,
                             height=150, key=f"llm_evidence_{current_task_id}")
                st.text_area("LLM Correction", value=task["correction"], disabled=True,
                             height=150, key=f"llm_correction_{current_task_id}")

            with human_col:
                st.markdown("#### Human Annotation (editable)")

                # All defaults are pre-seeded in session state on task switch (above)
                human_root_step = st.number_input(
                    "Root Error Step",
                    min_value=1,
                    max_value=task["total_steps"],
                    key="human_root_step",
                )

                tag_options = ALL_SUBTYPES + ["Other"]
                tag_display = [f"{t} \u2014 {SUBTYPE_DEFINITIONS[t]}" for t in ALL_SUBTYPES]
                tag_display.append("Other \u2014 custom tag (type below)")

                human_tag_selection = st.selectbox(
                    "Taxonomy Tag",
                    range(len(tag_options)),
                    format_func=lambda i: tag_display[i],
                    key="human_taxonomy_tag",
                )

                if tag_options[human_tag_selection] == "Other":
                    human_tag = st.text_input(
                        "Custom Taxonomy Tag",
                        placeholder="e.g. R14, P6, or a descriptive label",
                        key="human_custom_tag",
                    )
                else:
                    human_tag = tag_options[human_tag_selection]

                human_confidence = st.radio(
                    "Confidence",
                    CONFIDENCE_OPTIONS,
                    horizontal=True,
                    key="human_confidence",
                )

                human_evidence = st.text_area(
                    "Evidence",
                    height=150,
                    key="human_evidence",
                )

                human_correction = st.text_area(
                    "Correction",
                    height=150,
                    key="human_correction",
                )

            # Notes (full width, below the two columns)
            human_notes = st.text_area(
                "Additional Notes (optional)",
                placeholder="Any additional context or reasoning...",
                key="human_notes",
            )

            # Save button
            save_col, status_col = st.columns([1, 3])
            with save_col:
                save_clicked = st.button(
                    "Save Annotation" if not existing_annotation else "Update Annotation",
                    type="primary",
                    use_container_width=True,
                )

            if save_clicked:
                if not annotator_name.strip():
                    with status_col:
                        st.error("Please enter your annotator name before saving.")
                else:
                    human_values = {
                        "root_error_step": human_root_step,
                        "taxonomy_tag": human_tag,
                        "evidence": human_evidence,
                        "correction": human_correction,
                        "confidence": human_confidence,
                    }
                    saved_path = save_annotation(
                        trial_dir, task["task_id"], task,
                        human_values, annotator_name.strip(), human_notes,
                        chosen_debugger=st.session_state.get("_chosen_debugger_model"),
                    )
                    with status_col:
                        st.success(f"Annotation saved to {saved_path.name}")
                    st.rerun()

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # â”€â”€ Load full trajectory data â”€â”€
        full_traj = load_full_trajectory(traj_path)
        full_steps = full_traj.get("steps", []) if full_traj else []
        # Keep first entry per step_num (primary action, not sleep/screenshot follow-ups)
        full_by_step = {}
        for s in full_steps:
            if s["step_num"] not in full_by_step:
                full_by_step[s["step_num"]] = s

        # Load raw trajectory for timestamps (needed for a11y tree lookup)
        raw_entries = load_raw_trajectory(traj_path)
        raw_by_step = {}
        for rs in raw_entries:
            sn = rs.get("step_num")
            if sn is not None:
                raw_by_step[sn] = rs

        # Build complete step list: use RCA steps as base, fill in from full trajectory
        # Deduplicate by step_num (Claude agent format logs multiple sub-actions per step)
        rca_steps = task.get("steps", [])
        seen_steps: set[int] = set()

        # Merge: RCA step data + full trajectory data (first entry per step_num wins)
        all_steps = []
        for s in rca_steps:
            if s["step_num"] in seen_steps:
                continue
            seen_steps.add(s["step_num"])
            merged = dict(s)
            full = full_by_step.get(s["step_num"], {})
            merged["reasoning"] = full.get("reasoning", "")
            merged["llm_tool_use"] = full.get("llm_tool_use", merged.get("llm_tool_use", ""))
            # Use normalized action_code from full trajectory (model-scale
            # coordinates) so it matches the reasoning text.
            if full.get("action_code"):
                merged["action_code"] = full["action_code"]
            merged["screenshot"] = _remap_path(merged.get("screenshot"))
            merged["_from_rca"] = True
            all_steps.append(merged)

        # Add any steps from full trajectory that aren't already included
        for fs in full_steps:
            if fs["step_num"] in seen_steps:
                continue
            seen_steps.add(fs["step_num"])
            merged = {
                "step_num": fs["step_num"],
                "action_type": fs.get("action_type", "code"),
                "action_code": fs.get("action_code", ""),
                "error": fs.get("error", ""),
                "reward": fs.get("reward", 0),
                "done": fs.get("done", False),
                "screenshot": _remap_path(str(fs["screenshot_path"])) if fs.get("screenshot_path") else None,
                "reasoning": fs.get("reasoning", ""),
                "llm_tool_use": fs.get("llm_tool_use", ""),
                "action_input": fs.get("action_input", {}),
                "_from_rca": False,
            }
            all_steps.append(merged)

        all_steps.sort(key=lambda s: s["step_num"])

        # â”€â”€ Recording Video â”€â”€
        if traj_path:
            recording_path = Path(traj_path) / "recording.mp4"
            if _path_exists(recording_path):
                with st.expander("Recording", expanded=False):
                    st.video(_windows_long_path(recording_path))

        # â”€â”€ Trajectory â”€â”€
        root_step = task["root_error_step"]
        total_display = len(all_steps)
        error_count = sum(1 for s in all_steps if s.get("error"))
        st.subheader(f"Trajectory ({total_display} steps, {error_count} errors)")

        # Display options â€” horizontal row
        opt1, opt2, opt3, opt4 = st.columns(4)
        with opt1:
            show_only_errors = st.checkbox("Show only error steps", value=False)
        with opt2:
            show_coords = st.checkbox("Show click coordinates", value=True)
        with opt3:
            show_a11y = st.checkbox("Show a11y tree (LLM input)", value=False)
        with opt4:
            expand_reasoning = st.checkbox("Expand all reasoning", value=False)

        st.caption("Note: Coordinates are normalized to model-scale via load_normalized_trajectory(). Actual executed coordinates in traj.jsonl may differ (screen-scale).")

        # Apply filter
        if show_only_errors:
            display_steps = [s for s in all_steps if s.get("error") or s["step_num"] == task["root_error_step"]]
        else:
            display_steps = all_steps

        for step in display_steps:
            step_num = step["step_num"]
            is_root = step_num == root_step
            is_cascade = step_num > root_step and step.get("error")
            has_error = bool(step.get("error"))

            css_class, label, step_heading = step_status_display(step_num, root_step, has_error)
            st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)

            col_img, col_detail = st.columns([1.5, 1])

            with col_img:
                screenshot_path = step.get("screenshot")
                if screenshot_path and _path_exists(screenshot_path):
                    img = Image.open(_windows_long_path(screenshot_path))
                    caption_parts = [f"Step {step_num}"]

                    if show_coords and step.get("action_code"):
                        cx, cy = parse_click_coordinates(step["action_code"])
                        if cx is not None:
                            img = draw_click_marker(img, cx, cy)
                            caption_parts.append(f"Click: ({cx}, {cy})")

                    st.image(img, caption=" | ".join(caption_parts), use_container_width=True)
                else:
                    st.caption(f"Step {step_num} - no screenshot")

            with col_detail:
                st.markdown(f"#### {step_heading}")

                # Action code
                if step.get("action_code"):
                    st.code(step["action_code"], language="python")

                reasoning = step.get("reasoning", "")
                tool_use_text = step.get("llm_tool_use", "")
                tagged_tool_match = re.search(
                    r'\[TOOL_USE\]\s*(.*?)(?=\[THINKING\]|\[TEXT\]|$)',
                    reasoning,
                    re.DOTALL,
                )
                if tagged_tool_match and not tool_use_text:
                    tool_use_text = tagged_tool_match.group(1).strip()
                if tagged_tool_match:
                    reasoning = re.sub(
                        r'\[TOOL_USE\]\s*(.*?)(?=\[THINKING\]|\[TEXT\]|$)',
                        '',
                        reasoning,
                        flags=re.DOTALL,
                    ).strip()

                if tool_use_text:
                    with st.expander("Tool Use (LLM Action)", expanded=False):
                        st.code(tool_use_text[:1000], language="text")

                # Error
                if step.get("error"):
                    st.error(f"```\n{step['error'][:500]}\n```")

                # Step metadata
                st.caption(f"reward={step['reward']}  done={step['done']}  type={step.get('action_type', '?')}")

                # Agent reasoning (from raw_response / response / plan fields)
                if reasoning:
                    with st.expander("Agent Reasoning (LLM Response)", expanded=expand_reasoning):
                        # Parse [THINKING] and [TEXT] sections. Tool use is shown separately.
                        thinking = ""
                        text = ""

                        thinking_match = re.search(r'\[THINKING\]\s*(.*?)(?=\[TEXT\]|$)', reasoning, re.DOTALL)
                        text_match = re.search(r'\[TEXT\]\s*(.*?)(?=\[THINKING\]|$)', reasoning, re.DOTALL)

                        if thinking_match:
                            thinking = thinking_match.group(1).strip()
                        if text_match:
                            text = text_match.group(1).strip()

                        if thinking:
                            st.markdown("**THINKING:**")
                            st.info(thinking[:800])
                        if text:
                            st.markdown("**TEXT:**")
                            st.success(text[:500])

                        # Fallback: show raw if no sections found
                        if not (thinking or text):
                            for section in re.finditer(
                                r'(?ms)^([A-Z][A-Za-z ]+):\n(.*?)(?=^[A-Z][A-Za-z ]+:\n|\Z)',
                                reasoning,
                            ):
                                label = section.group(1).strip()
                                body = section.group(2).strip()
                                if body:
                                    st.markdown(f"**{label}:**")
                                    st.markdown(body[:1000])
                            if not re.search(r'(?m)^[A-Z][A-Za-z ]+:\n', reasoning):
                                st.markdown(reasoning[:1000])

                # A11y tree (LLM input)
                if show_a11y:
                    raw = raw_by_step.get(step_num, {})
                    ts = raw.get("action_timestamp", "")
                    a11y_text = load_a11y_tree(traj_path, step_num, ts)
                    if a11y_text:
                        with st.expander(f"A11y Tree (LLM Input) - {len(a11y_text)} chars"):
                            st.code(a11y_text[:3000], language="text")

                # RCA evidence inline for root step
                if is_root:
                    st.warning(f"**RCA Evidence:** {task['evidence']}")

            st.markdown('</div>', unsafe_allow_html=True)
            st.markdown("")

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("Debugger Memory")

        # Episodic memory record
        task_memory = [r for r in memory_records if r.get("task_id") == task["task_id"]]

        mem_col, rca_col = st.columns(2)

        with mem_col:
            st.markdown("#### Episodic Memory Record")
            if task_memory:
                task_memory.sort(key=lambda r: r.get("created_at", ""))
                for rec in task_memory:
                    type_label = {"Type1": "Failed", "Type2": "Successful", "Type3": "Debugged-success"}.get(rec["type"], rec["type"])
                    type_class = {"Type1": "tag-error", "Type2": "tag-ok", "Type3": "tag-purple"}.get(rec["type"], "tag-info")
                    st.markdown(
                        f'<span class="tag {type_class}">{type_label}</span>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"""
| Field | Value |
|-------|-------|
| **Memory ID** | `{rec['id'][:12]}...` |
| **Type** | {type_label} ({rec['type']}) |
| **App** | {rec.get('app_id', 'N/A')} |
| **Steps** | {rec['steps_count']} |
| **Terminal Step** | {rec['terminal_step']} |
| **Created** | {rec['created_at'][:19]} |
""")
                    with st.expander("Raw Memory JSON"):
                        st.json(rec)
            else:
                st.caption("No episodic memory records for this task yet.")

        with rca_col:
            st.markdown("#### RCA Analysis Result")
            st.markdown(f"""
| Field | Value |
|-------|-------|
| **Root Error Step** | Step {task['root_error_step']} |
| **Taxonomy Tag** | {task['taxonomy_tag']} |
| **Confidence** | {task['confidence']:.0%} |
| **Model** | {task.get('model', 'N/A')} |
| **Total Steps** | {task['total_steps']} |
| **Terminal Step** | {task.get('terminal_step', 'N/A')} |
| **Status** | {task.get('status', 'N/A')} |
| **Analyzed At** | {task.get('created_at', 'N/A')[:19]} |
""")
            st.markdown("**Evidence:**")
            st.error(task["evidence"])
            st.markdown("**Correction:**")
            st.success(task["correction"])

        # All memory records overview
        if len(memory_records) > 1:
            with st.expander(f"All Memory Records ({len(memory_records)} total)"):
                for rec in sorted(memory_records, key=lambda r: r.get("created_at", "")):
                    type_label = {"Type1": "Failed", "Type2": "Successful", "Type3": "Debugged-success"}.get(rec["type"], rec["type"])
                    type_class = {"Type1": "tag-error", "Type2": "tag-ok", "Type3": "tag-purple"}.get(rec["type"], "tag-info")
                    is_current = rec.get("task_id") == task["task_id"]
                    marker = " <- current" if is_current else ""
                    st.markdown(
                        f'<span class="tag {type_class}">{type_label}</span> '
                        f'`{rec.get("task_id", "?")[:12]}...` - '
                        f'{rec["steps_count"]} steps, terminal {rec["terminal_step"]} '
                        f'`{rec["created_at"][:19]}`{marker}',
                        unsafe_allow_html=True,
                    )

        with st.expander("Raw RCA JSON"):
            display = {k: v for k, v in task.items() if k != "steps"}
            st.json(display)


if __name__ == "__main__":
    main()
