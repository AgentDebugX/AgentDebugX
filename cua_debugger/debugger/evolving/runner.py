"""Evolving-taxonomy RCA runner (Phase 12).

Serial per-case driver that:
1. Loads the trajectory via debugger.ingester.ingest().
2. Calls the model with the empty-start system prompt + evolving tool schema
   AND the current taxonomy_state_so_far in the user message.
3. Parses the finish() call into (RCAResult-shaped dict, AuditEntry, new state).
4. Persists per-case output and (every 10 cases) a taxonomy snapshot.
5. After all cases, writes final_taxonomy.json and full audit_trail.jsonl.

Model identity is provider-agnostic â€” uses make_client(provider, model, base_url)
so provider-specific model names all work with
just CLI flag changes.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from debugger.agent import run_react_loop
from debugger.config import load_config
from debugger.ingester import IngestionResult
from debugger.pipeline.client_factory import make_client

from debugger.evolving import EvolvingOp, TaxonomyState, AuditEntry
from debugger.evolving.case_loader import (
    load_case_set,
    resolve_traj_dirs,
    shuffle_for_seed,
)
from debugger.evolving.prompts import EVOLVING_RCA_SYSTEM_PROMPT
from debugger.evolving.tools import get_evolving_tools

SNAPSHOT_EVERY_N_CASES = 10  # Claude's discretion per 12-CONTEXT.md

_cfg = load_config()


# --- per-case helpers (mirror rca.py) -----------------------------------

def _ingestion_to_traj_dict(ir: IngestionResult) -> dict:
    return {
        "task_id": ir.task_id,
        "instruction": ir.instruction,
        "result_score": None,
        "traj_dir": "",
        "format": ir.fmt,
        "steps": [s.to_dict() for s in ir.trajectory],
        "system_errors": [],
    }


def _format_ingestion_summary(ir: IngestionResult) -> str:
    lines = [
        f"Task ID: {ir.task_id}",
        f"Task: {ir.instruction or '(not available)'}",
        f"Status: {ir.status}",
        f"Terminal Failure Step (F): {ir.terminal_step}",
        f"Total steps: {len(ir.trajectory)}",
    ]
    if ir.is_infeasible:
        lines.append("Infeasible: YES â€” this task is designed to be impossible to complete")
    if ir.error_msg:
        lines.append(f"Failure reason: {ir.error_msg}")
    error_steps = [s.step_num for s in ir.trajectory if s.error]
    if error_steps:
        lines.append(f"Steps with execution errors: {error_steps}")
    return "\n".join(lines)


def format_taxonomy_state(state: TaxonomyState) -> str:
    """Render the current evolving taxonomy for inclusion in the user message."""
    if state.size() == 0:
        return "taxonomy_state_so_far: (empty â€” no subtypes discovered yet)"
    lines = ["taxonomy_state_so_far:"]
    for code, info in sorted(state.subtypes.items()):
        lines.append(
            f"  - {code} (parent: {info['parent']}): {info['name']} â€” "
            f"{info['definition']}"
        )
    return "\n".join(lines)


# --- per-case driver -----------------------------------------------------

def run_evolving_rca_on_case(
    traj_dir: Path,
    current_state: TaxonomyState,
    client,
    model: str,
    osworld_root: Path,
    verbose: bool = True,
    log_path: Path | None = None,
) -> tuple[dict, AuditEntry, TaxonomyState]:
    """Run one case end-to-end.

    Returns:
        (rca_dict, audit_entry, new_state)
        rca_dict has the same shape as debugger.rca.RCAResult fields plus task_id.
    """
    ir = IngestionResult.from_directory(traj_dir)
    traj_data = _ingestion_to_traj_dict(ir)
    summary = _format_ingestion_summary(ir)
    state_block = format_taxonomy_state(current_state)

    user_content = (
        "Perform Root Cause Analysis on this failed trajectory, then decide how "
        "to evolve the taxonomy:\n\n"
        f"{summary}\n\n"
        f"{state_block}\n\n"
        "The trajectory is already loaded. You can directly call "
        "get_step_details to inspect steps. Work backwards from the Terminal "
        "Failure Step, then call finish() with root_error_step, taxonomy_tag, "
        "evidence, correction, confidence, per_step_summaries, AND taxonomy_op."
    )

    finish_input, thinking, _ = run_react_loop(
        client=client,
        model=model,
        system_prompt=EVOLVING_RCA_SYSTEM_PROMPT,
        tools=get_evolving_tools(),
        messages=[{"role": "user", "content": user_content}],
        traj_data=traj_data,
        osworld_root=osworld_root,
        thinking_budget=_cfg.rca_thinking_budget,
        max_tokens=_cfg.rca_max_tokens,
        max_turns=_cfg.rca_max_turns,
        verbose=verbose,
        verbose_prefix=f"Evolving-RCA [{ir.task_id}]",
        log_path=log_path,
    )

    # Parse taxonomy_op and advance the state
    op_block = finish_input["taxonomy_op"]
    op_name = op_block["type"]
    op_args = op_block.get("op_args", {}) or {}
    op_reasoning = op_block.get("reasoning", "")

    new_state = current_state.apply_op(op_name, op_args)

    audit = AuditEntry(
        op=op_name,
        case_id=ir.task_id,
        timestamp=datetime.now().isoformat(),
        taxonomy_state_before=current_state.to_json(),
        taxonomy_state_after=new_state.to_json(),
        reasoning=op_reasoning,
        op_args=op_args,
    )

    rca_dict = {
        "task_id": ir.task_id,
        "model": model,
        "root_error_step": int(finish_input["root_error_step"]),
        "taxonomy_tag": str(finish_input["taxonomy_tag"]),
        "evidence": finish_input["evidence"],
        "correction": finish_input["correction"],
        "confidence": float(finish_input["confidence"]),
        "per_step_summaries": finish_input.get("per_step_summaries") or [],
        "taxonomy_op": {
            "type": op_name,
            "op_args": op_args,
            "reasoning": op_reasoning,
        },
        "thinking_trace_length": len(thinking),
    }
    return rca_dict, audit, new_state


# --- full-pipeline driver -----------------------------------------------

def _run_dir(output_root: Path, model: str, seed: int) -> Path:
    return Path(output_root) / model / f"seed_{seed}"


def run_full_evolving_pipeline(
    model: str,
    provider: str,
    base_url: str | None,
    seed: int,
    output_root: Path,
    osworld_root: Path,
    limit: int | None = None,
    verbose: bool = True,
    resume: bool = False,
) -> dict:
    """Process the full case set serially under (model, seed). Returns a summary dict.

    If resume=True and audit_trail.jsonl exists with entries, reconstructs
    TaxonomyState from the last entry and skips task_ids already in the trail.
    errored_cases.jsonl is wiped (prior errors were typically rate-limit).
    """
    run_dir = _run_dir(output_root, model, seed)
    snapshots_dir = run_dir / "taxonomy_snapshots"
    per_case_dir = run_dir / "per_case_rca"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    per_case_dir.mkdir(parents=True, exist_ok=True)
    audit_trail_path = run_dir / "audit_trail.jsonl"

    # Resolve base_url: explicit arg > config base_urls dict > env var (e.g.
    # GEMINI_BASE_URL is set by debugger.config.load_config from the api_keys
    # block of debugger.json)
    if base_url is None:
        base_urls = getattr(_cfg, "base_urls", {}) or {}
        base_url = base_urls.get(provider) or os.environ.get(f"{provider.upper()}_BASE_URL")

    client = make_client(provider, model, base_url)

    cases = shuffle_for_seed(load_case_set(), seed)
    if limit is not None:
        cases = cases[:limit]

    traj_dirs = resolve_traj_dirs(cases)

    state = TaxonomyState()
    completed_task_ids: set[str] = set()
    n_done = 0
    n_errored = 0
    n_missing = 0
    n_skipped_resume = 0
    errored_log_path = run_dir / "errored_cases.jsonl"

    if resume and audit_trail_path.exists() and audit_trail_path.stat().st_size > 0:
        audit_entries = [
            json.loads(l) for l in audit_trail_path.read_text(encoding="utf-8").splitlines() if l.strip()
        ]
        if audit_entries:
            last = audit_entries[-1]
            state = TaxonomyState.from_json(last["taxonomy_state_after"])
            completed_task_ids = {a["case_id"] for a in audit_entries}
            n_done = len(audit_entries)
            if verbose:
                print(
                    f"[resume] reconstructed state from {len(audit_entries)} prior entries, "
                    f"state_size={state.size()}, completed_task_ids={len(completed_task_ids)}"
                )
        # Wipe errored log on resume â€” prior errors were typically rate-limit and
        # we are about to retry those cases.
        errored_log_path.write_text("", encoding="utf-8")
    else:
        errored_log_path.write_text("", encoding="utf-8")
        # Truncate audit trail at the start of a fresh run so reruns are deterministic.
        audit_trail_path.write_text("", encoding="utf-8")

    for idx, task_id in enumerate(cases, start=1):
        if task_id in completed_task_ids:
            n_skipped_resume += 1
            if verbose:
                print(f"[{idx}/{len(cases)}] RESUME-SKIP already-completed: {task_id}")
            continue

        traj_dir = traj_dirs.get(task_id)
        if traj_dir is None or not traj_dir.exists():
            n_missing += 1
            if verbose:
                print(f"[{idx}/{len(cases)}] SKIP missing trajectory dir for: {task_id}")
            continue

        t0 = time.time()
        try:
            rca_dict, audit, new_state = run_evolving_rca_on_case(
                traj_dir=traj_dir,
                current_state=state,
                client=client,
                model=model,
                osworld_root=osworld_root,
                verbose=verbose,
            )
        except Exception as e:
            n_errored += 1
            with errored_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "task_id": task_id,
                    "error_type": type(e).__name__,
                    "error": str(e)[:500],
                    "idx": idx,
                }, ensure_ascii=False) + "\n")
            if verbose:
                print(f"[{idx}/{len(cases)}] ERROR on {task_id}: {e}")
            continue

        (per_case_dir / f"rca_{task_id}.json").write_text(
            json.dumps(rca_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        with audit_trail_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit.to_json(), ensure_ascii=False) + "\n")

        state = new_state
        n_done += 1

        if n_done % SNAPSHOT_EVERY_N_CASES == 0:
            snap_path = snapshots_dir / f"after_{n_done:04d}_cases.json"
            snap_path.write_text(
                json.dumps(state.to_json(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        if verbose:
            print(
                f"[{idx}/{len(cases)}] {task_id} op={audit.op} "
                f"state_size={state.size()} elapsed={time.time()-t0:.1f}s"
            )

    # Final taxonomy
    (run_dir / "final_taxonomy.json").write_text(
        json.dumps(state.to_json(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "model": model,
        "provider": provider,
        "seed": seed,
        "cases_processed": n_done,
        "n_errored": n_errored,
        "n_missing": n_missing,
        "n_skipped_resume": n_skipped_resume,
        "n_total": len(cases),
        "final_taxonomy_size": state.size(),
        "run_dir": str(run_dir),
        "errored_log": str(errored_log_path),
        "resumed": resume,
    }
