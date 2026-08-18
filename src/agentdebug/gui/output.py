"""
Output formatting for the trajectory debugger.

build_output  — assembles the final JSON report dict
print_summary — prints a human-readable summary to stdout
"""

import textwrap

from .taxonomy import TAXONOMY_CATEGORIES


def build_output(traj: dict, report: dict, model: str) -> dict:
    """Merge trajectory metadata with the agent report into one output dict."""
    return {
        "task_id": traj["task_id"],
        "instruction": traj["instruction"],
        "result_score": traj["result_score"],
        "trajectory_dir": traj["traj_dir"],
        "num_steps": len(traj["steps"]),
        "model_used": model,
        **report,
    }


def print_summary(output: dict) -> None:
    """Print a human-readable summary of the analysis report."""
    sep, thin = "=" * 60, "-" * 60

    print(sep)
    print(f"TRAJECTORY ANALYSIS: {output['task_id']}")
    print(sep)

    instr = output.get("instruction") or "(not available)"
    print(f"Task: {textwrap.shorten(instr, width=70, placeholder='...')}")

    score = output.get("result_score")
    score_str = str(score) if score is not None else "unknown"
    thinking = output.get("_thinking_trace", [])
    print(
        f"Steps: {output['num_steps']}  |  "
        f"Score: {score_str}  |  "
        f"Agent turns (w/ thinking): {len(thinking)}"
    )
    print(thin)

    primary = output.get("primary_error_category") or "None"
    print(f"PRIMARY ERROR: {primary}\n")

    taxonomy = output.get("error_taxonomy", {})
    if taxonomy:
        print("ERROR BREAKDOWN:")
        for cat in TAXONOMY_CATEGORIES:
            info = taxonomy.get(cat, {})
            if info.get("present"):
                steps_str = (
                    f"[steps: {', '.join(str(s) for s in info.get('steps', []))}]"
                    if info.get("steps") else ""
                )
                print(f"  \u2717 {cat:<42} {steps_str}")
            else:
                print(f"  \u2713 {cat:<42} (none)")

    summary = output.get("overall_summary", "")
    if summary:
        print("\nSUMMARY:")
        for line in textwrap.wrap(summary, width=68):
            print(f"  {line}")

    annotated = [
        s for s in output.get("per_step_analysis", [])
        if s.get("what_went_wrong")
    ]
    if annotated:
        print(f"\nSTEP ANNOTATIONS ({len(annotated)} error step(s)):")
        for s in annotated:
            cats = ", ".join(s.get("error_categories", [])) or "no category"
            print(f"\n  Step {s['step_num']}: {cats}")
            for field, label in [
                ("screen_state",    "Screen"),
                ("what_went_wrong", "Wrong "),
                ("correct_approach","Fix   "),
                ("root_cause",      "Root  "),
            ]:
                val = s.get(field, "")
                if not val:
                    continue
                wrapped = textwrap.wrap(val, width=54)
                print(f"    {label}: {wrapped[0]}")
                for rest in wrapped[1:]:
                    print(f"           {rest}")

    recs = output.get("recommendations", "")
    if recs:
        print("\nRECOMMENDATIONS:")
        for line in textwrap.wrap(recs, width=68):
            print(f"  {line}")

    print(thin)
