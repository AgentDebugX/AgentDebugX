import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent))

from debugger import IngestionResult, soft_tag_candidates
from debugger.config import load_config
from debugger.memory import (
    EpisodicMemory,
    LessonMemory,
    TrajectoryType,
    load_annotation,
    build_error_context,
    extract_intention,
    distill_lesson,
)
from debugger.utils import ABlockLogger
from debugger.taxonomy import SUBTYPE_DEFINITIONS

log = ABlockLogger(level="INFO")
log.set_role(role="debugger_pipeline")

# â”€â”€ trial directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _create_trial_dir(output_dir: Path, trial_name: str | None = None) -> Path:
    """Create and return a timestamped trial directory."""
    if trial_name:
        trial_dir = output_dir / trial_name
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trial_dir = output_dir / f"trial_{timestamp}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "rca").mkdir(exist_ok=True)
    (trial_dir / "rca_success").mkdir(exist_ok=True)
    return trial_dir


# â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def discover_trajectories(trajectory_dir: Path) -> list[Path]:
    """Find all trajectory directories under trajectory_dir."""
    dirs = []
    for jsonl in trajectory_dir.rglob("trajectory.jsonl"):
        dirs.append(jsonl.parent)
    for jsonl in trajectory_dir.rglob("traj.jsonl"):
        dirs.append(jsonl.parent)
    return sorted(set(dirs))


# ---------------------------------------------------------------------------
# Provider registry â€” which env-var prefix each OpenAI-compat provider reads.
#
# Each entry maps the logical provider name onto the environment-variable prefix used for its API key and optional base URL. Anthropic is handled separately because it uses the native Anthropic SDK, not an OpenAI-compatible transport.
# ---------------------------------------------------------------------------
# Providers reachable through the standard OpenAI-compatible
# ``/v1/chat/completions`` endpoint (gemini, qwen). Each entry maps the
# logical provider name to the env-var prefix that carries its key + base URL.
_OPENAI_COMPAT_PROVIDERS: dict[str, str] = {
    "openai": "OPENAI",
    "gemini": "GEMINI",
    "qwen":   "QWEN",
}

# Providers that require Perplexity's ``/v1/responses`` endpoint instead.
# Anthropic models on Perplexity 404 on ``/v1/chat/completions`` (verified
# 2026-05-22) so we route them through PerplexityResponsesAdapter.
_PERPLEXITY_RESPONSES_PROVIDERS: dict[str, str] = {
    "sonnet": "SONNET",   # Perplexity-routed Anthropic models
}


def make_client(provider: str, model: str):
    """Create an API client for the given provider.

    Supported providers:

    * ``"anthropic"`` â€” direct Anthropic API (Claude family); uses
                        ``ANTHROPIC_API_KEY``.
    * ``"openai"``   - OpenAI-compatible API; uses ``OPENAI_API_KEY`` and defaults to ``https://api.openai.com/v1``.
    * ``"gemini"``   - Gemini-compatible OpenAI proxy (``GEMINI_API_KEY`` + ``GEMINI_BASE_URL``).
    * ``"qwen"``      â€” Qwen-compatible OpenAI proxy
                        (``QWEN_API_KEY`` + ``QWEN_BASE_URL``).
    * ``"sonnet"``    â€” Perplexity, supports ``anthropic/claude-*`` model
                        identifiers via the standard
                        ``/v1/chat/completions`` endpoint
                        (``SONNET_API_KEY`` + ``SONNET_BASE_URL``).

    All keys and optional base URLs are read from environment variables. JSON config files must not contain secrets.
    """
    if provider == "anthropic":
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log.error("ERROR: Set ANTHROPIC_API_KEY environment variable.")
            sys.exit(1)
        return Anthropic(api_key=api_key)

    if provider in _OPENAI_COMPAT_PROVIDERS:
        from debugger.openai_adapter import OpenAICompatAdapter
        env_prefix = _OPENAI_COMPAT_PROVIDERS[provider]
        api_key  = os.environ.get(f"{env_prefix}_API_KEY",  "")
        base_url = os.environ.get(f"{env_prefix}_BASE_URL", "")
        if not base_url:
            log.error(
                f"ERROR: Set {env_prefix}_BASE_URL environment variable "
                f"(needed by provider '{provider}')."
            )
            sys.exit(1)
        return OpenAICompatAdapter(model=model, api_key=api_key, base_url=base_url)

    if provider in _PERPLEXITY_RESPONSES_PROVIDERS:
        from debugger.perplexity_adapter import PerplexityResponsesAdapter
        env_prefix = _PERPLEXITY_RESPONSES_PROVIDERS[provider]
        api_key  = os.environ.get(f"{env_prefix}_API_KEY",  "")
        base_url = os.environ.get(f"{env_prefix}_BASE_URL", "")
        if not base_url:
            log.error(
                f"ERROR: Set {env_prefix}_BASE_URL environment variable "
                f"(needed by provider '{provider}')."
            )
            sys.exit(1)
        return PerplexityResponsesAdapter(model=model, api_key=api_key, base_url=base_url)

    log.error(
        f"ERROR: Unknown provider '{provider}'. "
        f"Valid providers: anthropic, "
        f"{', '.join(sorted({*_OPENAI_COMPAT_PROVIDERS, *_PERPLEXITY_RESPONSES_PROVIDERS}))}."
    )
    sys.exit(1)


def resolve_provider(model: str) -> str:
    """Auto-resolve the API provider from a model identifier.

    Recognised patterns (case-insensitive; checked in order):

    * ``"gemini..."``       â†’ ``"gemini"``
    * ``"qwen..."``         â†’ ``"qwen"``
    * ``"anthropic/..."``   â†’ ``"sonnet"`` (Perplexity)
    * ``"claude..."``       â†’ ``"anthropic"`` (direct)

    Raises ``ValueError`` when no rule matches â€” the caller can either pick
    the provider explicitly via ``make_client(provider, model)`` or extend
    the rule set here.
    """
    model_lower = model.lower()
    if model_lower.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if model_lower.startswith("gemini"):
        return "gemini"
    if model_lower.startswith("qwen"):
        return "qwen"
    if model_lower.startswith("anthropic/"):
        return "sonnet"
    if model_lower.startswith("claude"):
        return "anthropic"

    raise ValueError(
        f"Cannot auto-resolve provider for model {model!r}. "
        "Recognised prefixes: gpt*, o*, gemini*, qwen*, anthropic/* "
        "(SONNET via Perplexity), claude* (direct Anthropic)."
    )


def print_ingestion(ir: IngestionResult, traj_dir: Path) -> None:
    """Pretty-print an ingestion result."""
    log.info("")
    log.info(f"{'='*60}")
    log.info(f"Directory:   {traj_dir.parent}")
    log.info(f"Task ID:     {ir.task_id}")
    log.info(f"Instruction: {ir.instruction[:80]}..." if len(ir.instruction) > 80 else f"  Instruction: {ir.instruction}")
    log.info(f"Format:      {ir.fmt}")
    log.info(f"Infeasible:  {'YES (task designed to be impossible)' if ir.is_infeasible else 'NO'}")
    log.info(f"Status:      {ir.status}")
    log.info(f"Steps:       {len(ir.trajectory)}")
    log.info(f"Terminal:    step {ir.terminal_step}")
    log.info(f"Error:       {ir.error_msg[:120] if ir.error_msg else '--'}")

    log.info("")
    log.info(f"Step details:")
    for s in ir.trajectory:
        err_flag = "[ERR]" if s.error else ""
        done_flag = "[DONE]" if s.done else ""
        log.info(f"\tstep {s.step_num:>3}: {s.action_type:<12} reward={s.reward} {err_flag} {done_flag}")
        if s.error:
            log.info(f"\t\terror: {s.error[:100]}")

    if ir.status == "failure" and ir.trajectory:
        last = ir.trajectory[-1]
        candidates = soft_tag_candidates(
            action_type=last.action_type,
            app_id=traj_dir.parent.name,
            visual_delta=0.0,
        )
        log.info("")
        log.info(f"Soft-tag candidates (heuristic, no LLM):")
        for tag, prob in candidates[:3]:
            log.info(f"\t{str(tag) + ':':25} {prob:.2f}")

    log.info(f"{'='*60}")


def _steps_to_dicts(ir: IngestionResult) -> list[dict]:
    """Convert ingestion steps to serialisable dicts."""
    return [
        {
            "step_num": s.step_num,
            "action_type": s.action_type,
            "action_code": s.action_code,
            "error": s.error,
            "reward": s.reward,
            "done": s.done,
            "screenshot": str(s.screenshot_path) if s.screenshot_path else None,
        }
        for s in ir.trajectory
    ]


def _summary_to_dict(summary) -> dict | None:
    if hasattr(summary, "model_dump"):
        return summary.model_dump(mode="python")
    if isinstance(summary, dict):
        return summary
    return None


def _merge_step_summaries(steps: list[dict], summaries: list[dict]) -> list[dict]:
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
        created_at: str
) -> None:
    if not per_step_summaries:
        return

    app_id = traj_dir.parent.name
    summary_file = trial_dir / "step_summaries" / app_id / f"{ir.task_id}.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
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
    summary_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

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


# â”€â”€ per-task RCA save â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _save_rca_result(
        trial_dir: Path,
        ir: IngestionResult,
        rca, model: str,
        traj_dir: Path
) -> Path:
    """Save one RCA result. IF1 goes to rca_success/, others to rca/. Returns path."""
    subdir = "rca_success" if rca.taxonomy_tag == "IF1" else "rca"
    rca_file = trial_dir / subdir / f"rca_{ir.task_id}.json"
    per_step_summaries = [
        summary for summary in (
            _summary_to_dict(s) for s in getattr(rca, "per_step_summaries", [])
        )
        if summary is not None
    ]
    steps = _merge_step_summaries(_steps_to_dicts(ir), per_step_summaries)
    created_at = datetime.now().isoformat()
    data = {
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
    }
    with open(rca_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
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


# â”€â”€ summary / config save â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _save_config(trial_dir: Path, provider: str, model: str,
                 trajectory_dir: Path) -> None:
    """Save run config snapshot."""
    config = {
        "provider": provider,
        "model": model,
        "trajectory_dir": str(trajectory_dir),
        "created_at": datetime.now().isoformat(),
    }
    with open(trial_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)


def _save_summary(trial_dir: Path, summary_entries: list[dict], provider: str,
                  model: str, trajectory_dir: Path) -> None:
    """Save aggregated summary.json at end of trial."""
    failures = [e for e in summary_entries if e["status"] == "failure" and e.get("taxonomy_tag")]
    successes = [e for e in summary_entries if e["status"] == "success"]
    success_after_debug = [e for e in summary_entries if e["status"] == "success_after_debug"]
    infeasible = [e for e in summary_entries if e.get("is_infeasible")]
    data = {
        "trial": trial_dir.name,
        "model": model,
        "provider": provider,
        "trajectory_dir": str(trajectory_dir),
        "created_at": datetime.now().isoformat(),
        "total": len(summary_entries),
        "failures_analyzed": len(failures),
        "infeasible_analyzed": len(infeasible),
        "success_after_debug": len(success_after_debug),
        "successes_skipped": len(successes),
        "results": summary_entries,
    }
    with open(trial_dir / "summary.json", "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info(f"\nSummary saved to {trial_dir / 'summary.json'}")


# â”€â”€ classify â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def classify_trajectories(trajectory_dir: Path) -> dict:
    """Classify trajectories in trajectory_dir by result.txt without moving files.

    Returns dict with 'success', 'failure', and 'skipped' lists of path strings.
    """
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

        if result_val == 1.0:
            classification["success"].append(str(traj_dir))
        else:
            classification["failure"].append(str(traj_dir))

    skipped_count = len(classification["skipped"])
    log.info(f"  [classify] {len(classification['failure'])} failures, "
             f"{len(classification['success'])} successes"
             f"{f', {skipped_count} skipped' if skipped_count else ''}")
    return classification


def _save_classification(trial_dir: Path, classification: dict) -> Path:
    """Save classification.json mapping trajectories to success/failure."""
    out_file = trial_dir / "classification.json"
    data = {
        "created_at": datetime.now().isoformat(),
        "total": sum(len(v) for v in classification.values()),
        **{k: len(v) for k, v in classification.items()},
        "trajectories": classification,
    }
    with open(out_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info(f"Classification saved to {out_file}")
    return out_file


# â”€â”€ per-task worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _process_single_task(
    traj_dir: Path,
    trial_dir: Path,
    model: str,
    client,
    osworld_root: Path,
    mem: EpisodicMemory,
    mem_lesson: LessonMemory,
    skip_existing: bool,
    task_index: int,
    total_tasks: int,
    lesson_mem: LessonMemory | None = None,
    lesson_top_k: int = 3,
) -> dict | None:
    """
    Process one trajectory (ingest -> RCA -> tag -> save). Thread-safe.

    Returns a summary entry dict, or None on ingest failure.
    """
    from debugger import run_rca
    from debugger.tagger import tag_from_rca

    tid = threading.current_thread().name
    prefix = f"[{tid}] [{task_index}/{total_tasks}]"

    log.info("")
    log.info(f"{'#'*60}")
    log.info(f"{prefix} Processing: {traj_dir.name}")
    log.info(f"{'#'*60}")

    # â”€â”€ Ingest â”€â”€
    try:
        ir = IngestionResult.from_directory(traj_dir)
        print_ingestion(ir, traj_dir)
    except Exception as e:
        log.info(f"{prefix} [INGEST FAIL] {e}")
        return None

    # â”€â”€ Skip existing? â”€â”€
    rca_file = trial_dir / "rca" / f"rca_{ir.task_id}.json"
    rca_success_file = trial_dir / "rca_success" / f"rca_{ir.task_id}.json"
    existing_file = None
    if skip_existing:
        if rca_file.exists():
            existing_file = rca_file
        elif rca_success_file.exists():
            existing_file = rca_success_file
    if existing_file:
        log.info(f"{prefix}   -> Skipping (result exists): {existing_file}")
        with open(existing_file) as f:
            existing = json.load(f)
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
        traj_dict = {
            "task_id": ir.task_id,
            "instruction": ir.instruction,
            "traj_dir": str(traj_dir.resolve()),
            "steps": _steps_to_dicts(ir),
        }
        mem.add(traj_dict, TrajectoryType.TYPE2, metadata={"app_id": traj_dir.parent.name})
        return {"task_id": ir.task_id, "status": "success"}

    # â”€â”€ Retrieve lessons â”€â”€
    lessons = None
    if lesson_mem is not None and ir.instruction:
        try:
            app_id = traj_dir.parent.name
            lessons = lesson_mem.retrieve(
                ir.instruction, top_k=lesson_top_k, app_id=app_id
            )
            if lessons:
                log.info(f"{prefix}   Retrieved {len(lessons)} lesson(s) for injection (app={app_id})")
        except Exception as e:
            log.info(f"{prefix}   [LESSON RETRIEVE WARN] {e}")
            lessons = None

    # â”€â”€ RCA â”€â”€
    if ir.is_infeasible:
        log.info(f"{prefix}   Running infeasible-task analysis "
                 f"(agent {'succeeded' if ir.status == 'success' else 'failed'})...")
    else:
        log.info(f"{prefix}   Running RCA (this may take a minute)...")
    try:
        rca = run_rca(ir, model, client, osworld_root, verbose=True,
                      lessons=lessons)
    except Exception as e:
        log.info(f"{prefix}   [RCA FAIL] {e}")
        traj_dict = {
            "task_id": ir.task_id,
            "instruction": ir.instruction,
            "traj_dir": str(traj_dir.resolve()),
            "steps": _steps_to_dicts(ir),
        }
        mem.add(traj_dict, TrajectoryType.TYPE1, metadata={"app_id": traj_dir.parent.name})
        return {
            "task_id": ir.task_id,
            "status": "failure",
            "error": str(e)[:200],
        }

    # â”€â”€ Tag validation â”€â”€
    tag = tag_from_rca(rca)
    log.info(f"\n{prefix}   RCA Results:")
    log.info(f"    Root error step : {rca.root_error_step}")
    log.info(f"    Taxonomy tag    : {rca.taxonomy_tag}")
    log.info(f"    Validated tag   : {tag}")
    log.info(f"    Evidence        : {rca.evidence[:150]}")
    log.info(f"    Correction      : {rca.correction[:150]}")
    log.info(f"    Confidence      : {rca.confidence:.2f}")

    # â”€â”€ Persist RCA result â”€â”€
    _save_rca_result(trial_dir, ir, rca, model, traj_dir)

    # â”€â”€ Step 0: Load annotation (human preferred) â”€â”€
    annotation = load_annotation(trial_dir, ir.task_id)
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

    # â”€â”€ Step 1: Build EC_t and extract intention â”€â”€
    error_step = annotation.root_error_step if annotation else rca.root_error_step
    ec_t = build_error_context(ir, error_step=error_step)
    try:
        intention = extract_intention(
            ec_t, client=client, model=model, instruction=ir.instruction
        )
    except Exception as e:
        log.info(f"{prefix}   [INTENTION FAIL] {e}")
        intention = ""

    # â”€â”€ Step 1: Store extended episodic record â”€â”€
    traj_dict = {
        "task_id": ir.task_id,
        "instruction": ir.instruction,
        "traj_dir": str(traj_dir.resolve()),
        "steps": _steps_to_dicts(ir),
        "rca_analysis": {
            "root_error_step": rca.root_error_step,
            "taxonomy_tag": rca.taxonomy_tag,
            "evidence": rca.evidence,
            "correction": rca.correction,
            "confidence": rca.confidence,
            "model": model,
        },
    }
    episodic_id = mem.add(
        traj_dict,
        TrajectoryType.TYPE1,
        metadata={"app_id": traj_dir.parent.name},
        error_context=ec_t,
        agent_intention=intention,
        taxonomy_tag=rca.taxonomy_tag,
        error_step=error_step,
        annotation=annotation_dict,
    )

    # â”€â”€ Step 2A: Distill Lesson and persist as JSON â”€â”€
    rca_payload = {
        "root_error_step": rca.root_error_step,
        "taxonomy_tag": rca.taxonomy_tag,
        "evidence": rca.evidence,
        "correction": rca.correction,
        "confidence": rca.confidence,
    }
    try:
        lesson = distill_lesson(
            ec_t=ec_t,
            rca=rca_payload,
            intention=intention,
            client=client,
            model=model,
            app_id=traj_dir.parent.name,
            episodic_ref=episodic_id,
        )
        lessons_dir = trial_dir / "lessons"
        lessons_dir.mkdir(exist_ok=True)
        (lessons_dir / f"{ir.task_id}.json").write_text(
            json.dumps(lesson.model_dump(mode='json'), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    except Exception as e:
        log.info(f"{prefix}   [DISTILL FAIL] {e}")

    return {
        "task_id": ir.task_id,
        "status": "failure",
        "taxonomy_tag": rca.taxonomy_tag,
        "root_error_step": rca.root_error_step,
        "confidence": rca.confidence,
    }


# â”€â”€ main pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def cmd_full_pipeline(trajectory_dir: Path, provider: str, model: str,
                      output_dir: Path, trial_name: str | None = None,
                      skip_existing: bool = False,
                      workers: int = 4,
                      use_lessons: bool = False,
                      lesson_top_k: int = 3,
                      lesson_db_folder: str = "",
                      embd_model: str = "text-embedding-3-small",
                      task_ids_file: str = "") -> None:
    """Full pipeline: classify -> ingest -> RCA -> tagger -> memory."""

    # â”€â”€ Create trial directory â”€â”€
    trial_dir = _create_trial_dir(output_dir, trial_name)
    log.bind_file(trial_dir / "debugger.log")
    log.info(f"Trial directory: {trial_dir}")

    _save_config(trial_dir, provider, model, trajectory_dir)

    # â”€â”€ Phase 0: Classify trajectories (no file moves) â”€â”€
    log.info("Classifying trajectories...")
    classification = classify_trajectories(trajectory_dir)
    _save_classification(trial_dir, classification)

    client = make_client(provider, model)
    osworld_root = Path(".")
    mem = EpisodicMemory(data_file=trial_dir / "episodic.json")

    # â”€â”€ Lesson memory (optional) â”€â”€
    lesson_mem = None
    if use_lessons:
        db_folder = Path(lesson_db_folder) if lesson_db_folder else trial_dir / "lesson_memory"
        embd_key = os.environ.get("EMBD_API_KEY", "")
        embd_url = os.environ.get("EMBD_BASE_URL", "")
        if embd_key and db_folder.exists():
            lesson_mem = LessonMemory(
                api_key=embd_key,
                base_url=embd_url or None,
                model=embd_model,
                db_folder=db_folder,
                log_file=trial_dir / "lesson_memory.log",
            )
            log.info(f"LessonMemory loaded: {len(lesson_mem)} lessons from {db_folder}")
        else:
            if not embd_key:
                log.info("use_lessons=True but EMBD_API_KEY not set, skipping lesson injection")
            elif not db_folder.exists():
                log.info(f"use_lessons=True but lesson DB not found at {db_folder}, skipping")


    # Only process failure trajectories.
    # Infeasible tasks that succeeded are skipped â€” OSWorld guarantees
    # success + infeasible means the agent recognised infeasibility.
    failure_paths = set(classification["failure"])
    traj_dirs = [Path(p) for p in failure_paths]

    # Optional: filter to specific task IDs
    if task_ids_file:
        allowed_ids = set(json.loads(Path(task_ids_file).read_text(encoding="utf-8")))
        traj_dirs = [d for d in traj_dirs if d.name in allowed_ids]
        log.info(f"Filtered to {len(traj_dirs)} tasks from {task_ids_file}")

    if not traj_dirs:
        log.info("No failure trajectories to analyze.")
        return

    total = len(traj_dirs)
    log.info(f"Found {total} failure trajectories to analyze ({len(classification['success'])} successes skipped)")
    log.info(f"Provider: {provider}")
    log.info(f"Model: {model}")
    log.info(f"Workers: {workers}\n")

    # â”€â”€ Process tasks (parallel when workers > 1) â”€â”€
    summary_entries: list[dict] = []

    if workers <= 1:
        # Sequential mode (preserves old behavior)
        for idx, traj_dir in enumerate(traj_dirs, 1):
            entry = _process_single_task(
                traj_dir, trial_dir, model, client, osworld_root,
                mem, skip_existing, idx, total,
                lesson_mem=lesson_mem, lesson_top_k=lesson_top_k,
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
                    _process_single_task,
                    traj_dir, trial_dir, model, client, osworld_root,
                    mem, skip_existing, idx, total,
                    lesson_mem, lesson_top_k,
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

    # â”€â”€ Update classification with success_after_debug â”€â”€
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
        _save_classification(trial_dir, classification)
        log.info(f"  [classify] Moved {len(if1_task_ids)} IF1 tasks to success_after_debug")

    # â”€â”€ Save summary â”€â”€
    _save_summary(trial_dir, summary_entries, provider, model, trajectory_dir)

    # â”€â”€ Print memory state â”€â”€
    all_records = mem.list()
    log.info(f"\nâ”€â”€ Memory State ({len(all_records)} records) â”€â”€")
    for rec in all_records:
        log.info(f"  [{rec['type']}] {rec['task_id']} | {rec['app_id']} | "
                 f"{rec['steps_count']} steps | {rec['created_at'][:19]}")


# â”€â”€ show / reset memory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def cmd_show_memory(trial_dir: Path = None) -> None:
    """Display memory contents for a trial (or default location)."""
    if trial_dir:
        mem = EpisodicMemory(data_file=trial_dir / "episodic.json")
    else:
        mem = EpisodicMemory()
    all_records = mem.list()

    if not all_records:
        log.info("Memory is empty (no records stored yet).")
        return
    else:
        log.info(f"Episodic Memory: {len(all_records)} records\n")

    type_counts: dict[str, int] = {}
    for rec in all_records:
        t = rec["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    log.info("Summary:")
    for t, c in sorted(type_counts.items()):
        label = {"Type1": "Failed", "Type2": "Successful", "Type3": "Debugged-success"}.get(t, t)
        log.info(f"  {label} ({t}): {c}")

    log.info("")
    log.info(f"All records:")
    for rec in all_records:
        log.info("")
        log.info(f"\tID        : {rec['id']}")
        log.info(f"\tType      : {rec['type']}")
        log.info(f"\tTask ID   : {rec['task_id']}")
        log.info(f"\tApp ID    : {rec['app_id']}")
        log.info(f"\tSteps     : {rec['steps_count']}")
        log.info(f"\tTerminal  : {rec['terminal_step']}")
        log.info(f"\tCreated   : {rec['created_at']}")
        log.info(f"\tPath      : {rec['traj_path']}")


# â”€â”€ main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == "__main__":
    cfg = load_config()

    parser = argparse.ArgumentParser(description="GUI Agent Debugger pipeline")
    parser.add_argument("--show-memory", action="store_true",
                        help="Display current episodic memory contents")
    parser.add_argument("--reset-memory", action="store_true",
                        help="Reset episodic memory to empty")
    parser.add_argument("--trajectory-dir", type=Path, default=None,
                        help=f"Path to input trajectories (config: {cfg.trajectory_dir})")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help=f"Output base dir (config: {cfg.output_dir})")
    parser.add_argument("--trial-name", type=str, default=None,
                        help=f"Custom trial folder name (config: {cfg.trial_name}, default: trial_YYYYMMDD_HHMMSS)")
    parser.add_argument("--skip-existing", action="store_true", default=None,
                        help=f"Skip tasks that already have rca_{{task_id}}.json (config: {cfg.skip_existing})")
    parser.add_argument("--provider", type=str, default=None,
                        choices=["openai", "anthropic", "together", "gemini", "qwen", "sonnet"],
                        help=f"API provider (config: {cfg.provider})")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Override model (config: {cfg.model})")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"Number of parallel worker threads (config: {cfg.workers}, use 1 for sequential)")
    parser.add_argument("--use-lessons", action="store_true", default=None,
                        help=f"Enable lesson-augmented RCA (config: {cfg.use_lessons})")
    parser.add_argument("--lesson-top-k", type=int, default=None,
                        help=f"Number of lessons to retrieve per task (config: {cfg.lesson_top_k})")
    parser.add_argument("--lesson-db-folder", type=str, default=None,
                        help=f"Path to lesson Chroma DB (config: {cfg.lesson_db_folder or '<trial_dir>/lesson_memory'})")
    parser.add_argument("--task-ids-file", type=str, default="",
                        help="JSON file with list of task IDs to process (filter)")
    args = parser.parse_args()

    # CLI args override config
    provider = args.provider or cfg.provider
    model = args.model or cfg.model
    trajectory_dir = args.trajectory_dir or cfg.trajectory_dir
    output_dir = args.output_dir or cfg.output_dir
    workers = args.workers if args.workers is not None else cfg.workers
    skip_existing = args.skip_existing if args.skip_existing is not None else cfg.skip_existing
    trial_name = args.trial_name or cfg.trial_name
    use_lessons = args.use_lessons if args.use_lessons is not None else cfg.use_lessons
    lesson_top_k = args.lesson_top_k if args.lesson_top_k is not None else cfg.lesson_top_k
    lesson_db_folder = args.lesson_db_folder or cfg.lesson_db_folder

    if args.show_memory:
        cmd_show_memory()
    elif args.reset_memory:
        mem = EpisodicMemory()
        db_path = mem._path
        with open(db_path, "w") as f:
            json.dump({"version": 1, "records": {}}, f, indent=2)
        log.info(f"Memory reset: {db_path}")
    else:
        cmd_full_pipeline(
            trajectory_dir,
            provider,
            model,
            output_dir,
            trial_name=trial_name,
            skip_existing=skip_existing,
            workers=workers,
            use_lessons=use_lessons,
            lesson_top_k=lesson_top_k,
            lesson_db_folder=lesson_db_folder,
            embd_model=cfg.embd_model,
            task_ids_file=args.task_ids_file,
        )




