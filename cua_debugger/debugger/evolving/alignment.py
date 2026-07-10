"""LLM-as-judge alignment from evolved taxonomy to human taxonomy + projection.

Locked decisions (12-CONTEXT.md):
- Multi-to-one mapping (each evolved code -> single human code or NONE).
- Judge sees: evolved name + definition + up to 3 example cases + full human taxonomy.
- Coverage / purity / NMI as defined in this plan.
- Projected accuracy uses debugger.eval.compute_overall/compute_tag_metrics — no fresh impl.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from debugger.evolving import TaxonomyState
from debugger.taxonomy import SUBTYPE_DEFINITIONS, ALL_SUBTYPES

JUDGE_SYSTEM_PROMPT = """\
You are an expert taxonomy aligner. Given a candidate error subtype
(name + definition + 3 representative cases) and the full human reference
taxonomy, return the SINGLE best-matching human subtype code, or the literal
string NONE if no human subtype is a good match.

Output ONLY the code (e.g., G1, R10, IF2) or NONE. No preamble, no explanation.
"""


def _format_human_taxonomy() -> str:
    lines = ["Human taxonomy (choose ONE code from this list, or NONE):"]
    for code in ALL_SUBTYPES:
        lines.append(f"  - {code}: {SUBTYPE_DEFINITIONS[code]}")
    return "\n".join(lines)


def _format_evolved_subtype(code: str, info: dict, examples: list[dict]) -> str:
    lines = [
        f"Candidate evolved subtype:",
        f"  code: {code}",
        f"  name: {info.get('name', '')}",
        f"  definition: {info.get('definition', '')}",
        f"  parent_category: {info.get('parent', '')}",
        "",
        "Up to 3 example cases assigned this code:",
    ]
    for i, ex in enumerate(examples[:3], start=1):
        lines.append(f"  {i}. task_id={ex.get('task_id','?')}")
        lines.append(f"     evidence: {ex.get('evidence','')[:300]}")
        lines.append(f"     correction: {ex.get('correction','')[:300]}")
    return "\n".join(lines)


def load_run_artifacts(run_dir: Path) -> dict:
    """Load final_taxonomy, per_case RCAs, and build examples_by_code mapping.

    Returns a dict with:
      - final_taxonomy: TaxonomyState
      - per_case_rca: list of RCA dicts
      - examples_by_code: {code: [up to 3 RCAs sorted by confidence desc]}
    """
    run_dir = Path(run_dir)
    ft = TaxonomyState.from_json(
        json.loads((run_dir / "final_taxonomy.json").read_text(encoding="utf-8"))
    )
    per_case_rca = []
    for f in sorted((run_dir / "per_case_rca").glob("rca_*.json")):
        per_case_rca.append(json.loads(f.read_text(encoding="utf-8")))

    examples_by_code: dict[str, list[dict]] = {}
    sorted_rcas = sorted(per_case_rca, key=lambda r: r.get("confidence", 0.0), reverse=True)
    for r in sorted_rcas:
        tag = r.get("taxonomy_tag", "")
        if not tag:
            continue
        examples_by_code.setdefault(tag, [])
        if len(examples_by_code[tag]) < 3:
            examples_by_code[tag].append(r)

    return {
        "final_taxonomy": ft,
        "per_case_rca": per_case_rca,
        "examples_by_code": examples_by_code,
    }


def align_evolved_to_human(
    final_taxonomy: TaxonomyState,
    examples_by_code: dict[str, list[dict]],
    judge_client,
    judge_model: str,
    max_tokens: int = 32,
) -> dict[str, str]:
    """One judge call per evolved subtype. Returns {evolved_code: human_code_or_NONE}."""
    mapping: dict[str, str] = {}
    valid_outputs = set(ALL_SUBTYPES) | {"NONE"}
    for code, info in final_taxonomy.subtypes.items():
        examples = examples_by_code.get(code, [])
        user_content = (
            f"{_format_evolved_subtype(code, info, examples)}\n\n"
            f"{_format_human_taxonomy()}\n\n"
            "Return ONLY the single best-matching human subtype code or NONE."
        )
        resp = judge_client.messages.create(
            model=judge_model,
            max_tokens=max_tokens,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        # Anthropic-style response: resp.content[0].text
        text = ""
        try:
            text = resp.content[0].text.strip()
        except Exception:
            text = str(resp).strip()
        # Take the first whitespace-delimited token to defend against verbose judges.
        first = text.split()[0] if text else "NONE"
        mapping[code] = first if first in valid_outputs else "NONE"
    return mapping


def project_predictions(
    per_case_rca: list[dict],
    mapping: dict[str, str],
    annotations_dir: Path,
) -> list[dict]:
    """Build paired predictions for scoring with compute_overall/compute_tag_metrics.

    For each RCA, applies the evolved->human mapping and pairs against annotations.
    Drops rows where mapping returns NONE or annotation is missing.

    Returns list of dicts with keys:
      task_id, llm_tag, llm_step, llm_confidence, human_tag, human_step, annotator
    """
    annotations_dir = Path(annotations_dir)
    pairs: list[dict] = []
    for r in per_case_rca:
        evolved_tag = r.get("taxonomy_tag", "")
        projected = mapping.get(evolved_tag, "NONE")
        if projected == "NONE":
            continue
        task_id = r.get("task_id", "")
        ann_file = annotations_dir / f"human_{task_id}.json"
        if not ann_file.exists():
            continue
        try:
            ann = json.loads(ann_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        hv = ann.get("human_values")
        if isinstance(hv, list):
            hv = hv[0] if hv else None
        if not hv or hv.get("taxonomy_tag") is None or hv.get("root_error_step") is None:
            continue
        pairs.append({
            "task_id": task_id,
            "llm_tag": projected,
            "llm_step": int(r.get("root_error_step", 0)),
            "llm_confidence": float(r.get("confidence", 0.0)),
            "human_tag": str(hv["taxonomy_tag"]),
            "human_step": int(hv["root_error_step"]),
            "annotator": hv.get("annotator", ann.get("annotator", "unknown")),
        })
    return pairs


def _nmi(evolved_labels: list[str], human_labels: list[str]) -> float:
    """Compute normalized mutual information, with sklearn fallback to inline."""
    try:
        from sklearn.metrics import normalized_mutual_info_score
        return float(normalized_mutual_info_score(evolved_labels, human_labels))
    except Exception:
        # Inline fallback: NMI = MI / sqrt(H(X) * H(Y)).
        from collections import Counter
        n = len(evolved_labels)
        if n == 0:
            return 0.0
        def H(labels):
            c = Counter(labels)
            return -sum((v / n) * math.log(v / n) for v in c.values() if v > 0)
        joint = Counter(zip(evolved_labels, human_labels))
        px = Counter(evolved_labels)
        py = Counter(human_labels)
        mi = 0.0
        for (x, y), v in joint.items():
            pxy = v / n
            mi += pxy * math.log(pxy / ((px[x] / n) * (py[y] / n)))
        hx, hy = H(evolved_labels), H(human_labels)
        denom = math.sqrt(hx * hy) if hx > 0 and hy > 0 else 0.0
        return float(mi / denom) if denom > 0 else 0.0


def compute_coverage_purity_nmi(
    mapping: dict[str, str],
    per_case_rca: list[dict],
    annotations_dir: Path,
) -> dict[str, Any]:
    """Compute coverage, purity, and NMI metrics.

    - coverage: fraction of human subtypes actually used (in annotations) that
      have ≥1 evolved subtype mapping to them.
    - purity: fraction of evolved codes that map to a non-NONE human subtype.
    - nmi: normalized mutual information between evolved and human labels.

    Returns dict with coverage, purity, nmi, n_pairs, n_evolved_codes, human_subtypes_used.
    """
    annotations_dir = Path(annotations_dir)
    # Build the paired human labels for NMI and to define "human_subtypes_used"
    evolved_labels: list[str] = []
    human_labels: list[str] = []
    for r in per_case_rca:
        tag = r.get("taxonomy_tag", "")
        if not tag:
            continue
        task_id = r.get("task_id", "")
        ann_file = annotations_dir / f"human_{task_id}.json"
        if not ann_file.exists():
            continue
        try:
            ann = json.loads(ann_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        hv = ann.get("human_values")
        if isinstance(hv, list):
            hv = hv[0] if hv else None
        if not hv or hv.get("taxonomy_tag") is None:
            continue
        evolved_labels.append(tag)
        human_labels.append(str(hv["taxonomy_tag"]))

    human_subtypes_used = sorted(set(human_labels))
    mapped_humans = {v for v in mapping.values() if v != "NONE"}
    covered = mapped_humans & set(human_subtypes_used)
    coverage = len(covered) / len(human_subtypes_used) if human_subtypes_used else 0.0
    purity = sum(1 for v in mapping.values() if v != "NONE") / max(len(mapping), 1)
    nmi = _nmi(evolved_labels, human_labels)
    return {
        "coverage": round(coverage, 4),
        "purity": round(purity, 4),
        "nmi": round(nmi, 4),
        "n_pairs": len(evolved_labels),
        "n_evolved_codes": len(mapping),
        "human_subtypes_used": human_subtypes_used,
    }
