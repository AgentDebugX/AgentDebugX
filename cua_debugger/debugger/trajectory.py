"""
Trajectory loading for computer-use agent traces.

Supports two on-disk formats:

  Claude-agent format  traj.jsonl
    - action field is a dict with keys: action_type, command, raw_response
    - screenshots are in the same directory, referenced by screenshot_file

  Other agent format   traj.jsonl
    - action field may be a raw pyautogui code string
    - reasoning may live in response / full_plan / reflection fields
    - screenshots are in the same directory, referenced by screenshot_file

  Legacy format        trajectory.jsonl
    - action field is a raw pyautogui code string
    - screenshots are in a screenshots/ subdirectory
    - task config lives alongside as {task_id}.json
"""

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Optional

# Max dimension for screenshots sent via API (keeps payload under Together's limit)
_MAX_SCREENSHOT_DIM = 768
_XML_TOOL_CALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
_XML_FUNCTION_RE = re.compile(r"<function=([^>]+)>(.*?)</function>", re.DOTALL)
_XML_PARAMETER_RE = re.compile(r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>", re.DOTALL)
_TAGGED_TOOL_USE_RE = re.compile(
    r"\[TOOL_USE\]\s*(.*?)(?=\[THINKING\]|\[TEXT\]|\[OTHER\]|\Z)",
    re.DOTALL,
)
_TASK_INTENT_PATTERNS = (
    re.compile(r"\bThe user wants me to\s+(.+?)(?:[.!?](?:\s|$)|$)", re.IGNORECASE),
    re.compile(r"\b(?:The )?user (?:asked|wants) (?:me )?to\s+(.+?)(?:[.!?](?:\s|$)|$)", re.IGNORECASE),
    re.compile(r"\bI(?:'ll| will) help you\s+(.+?)(?:[.!?](?:\s|$)|$)", re.IGNORECASE),
)
_INSTRUCTION_KEYS = ("instruction", "task_instruction", "goal", "objective")


def _windows_long_path(path: Path) -> str:
    resolved = str(Path(path).resolve(strict=False))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _path_exists(path: Path) -> bool:
    return os.path.exists(_windows_long_path(path))


def _read_image_b64(path: Optional[Path]) -> Optional[str]:
    if not path or not _path_exists(path):
        return None
    try:
        from PIL import Image
        img = Image.open(_windows_long_path(path))
        # Resize if too large
        if max(img.size) > _MAX_SCREENSHOT_DIM:
            img.thumbnail((_MAX_SCREENSHOT_DIM, _MAX_SCREENSHOT_DIM), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    except ImportError:
        # Fallback: send raw if Pillow not available
        with open(_windows_long_path(path), "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")


def image_block(path: Optional[Path]) -> Optional[dict]:
    """Return an Anthropic image content block or None if path is missing."""
    b64 = _read_image_b64(path)
    if not b64:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def _parse_raw_response(raw: str) -> str:
    """
    Extract [TEXT] and [THINKING] content from a tagged raw_response string.\n\n    The supported tagged format is:
      [TEXT] ...
      [THINKING] ...
      [TOOL_USE] ...
      [OTHER] ...
    """
    lines = raw.splitlines()
    parts, current_tag, current_lines = [], None, []
    for line in lines:
        if line.startswith("[TEXT]"):
            if current_tag in ("[TEXT]", "[THINKING]") and current_lines:
                parts.append(" ".join(current_lines).strip())
            current_tag, current_lines = "[TEXT]", [line[6:].strip()]
        elif line.startswith("[THINKING]"):
            if current_tag in ("[TEXT]", "[THINKING]") and current_lines:
                parts.append(" ".join(current_lines).strip())
            current_tag, current_lines = "[THINKING]", [line[10:].strip()]
        elif line.startswith(("[TOOL_USE]", "[OTHER]")):
            if current_tag in ("[TEXT]", "[THINKING]") and current_lines:
                parts.append(" ".join(current_lines).strip())
            current_tag, current_lines = None, []
        else:
            if current_tag in ("[TEXT]", "[THINKING]"):
                current_lines.append(line)
    if current_tag in ("[TEXT]", "[THINKING]") and current_lines:
        parts.append(" ".join(current_lines).strip())
    return "\n".join(p for p in parts if p)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value).strip()


def _format_instruction(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" \"'")
    if not text:
        return ""
    if text[0].islower():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def infer_instruction_from_text(value) -> str:
    """Best-effort instruction recovery from model text when metadata is absent."""
    text = _as_text(value)
    if not text:
        return ""
    text = re.sub(r"\[[A-Z_]+\]\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern in _TASK_INTENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return _format_instruction(match.group(1))
    return ""


def infer_instruction_from_entry(entry: dict) -> str:
    """Recover an instruction from a JSONL entry if it carries one."""
    for key in _INSTRUCTION_KEYS:
        direct = _as_text(entry.get(key))
        if direct:
            return _format_instruction(direct)

    info = entry.get("info", {})
    if isinstance(info, dict):
        for key in _INSTRUCTION_KEYS:
            direct = _as_text(info.get(key))
            if direct:
                return _format_instruction(direct)

    action_raw = entry.get("action", {})
    candidates = []
    if isinstance(action_raw, dict):
        candidates.append(action_raw.get("raw_response"))
    candidates.extend(
        [
            entry.get("response"),
            entry.get("full_plan"),
            entry.get("reflection"),
        ]
    )
    for candidate in candidates:
        instruction = infer_instruction_from_text(candidate)
        if instruction:
            return instruction
    return ""


def _append_reasoning_section(parts: list[str], seen: set[str], label: str, value) -> None:
    text = _as_text(value)
    if not text or text in seen:
        return
    seen.add(text)
    parts.append(f"{label}:\n{text}")


def _clean_model_response(text: str) -> str:
    text = _XML_TOOL_CALL_RE.sub("", text)
    return text.strip()


def _extract_xml_tool_use(text: str) -> str:
    lines: list[str] = []
    for tool_call in _XML_TOOL_CALL_RE.findall(text):
        functions = _XML_FUNCTION_RE.findall(tool_call)
        if not functions:
            lines.append(tool_call.strip())
            continue
        for function_name, function_body in functions:
            if lines:
                lines.append("")
            lines.append(f"function: {function_name.strip()}")
            for param_name, param_value in _XML_PARAMETER_RE.findall(function_body):
                lines.append(f"{param_name.strip()}: {param_value.strip()}")
    return "\n".join(lines).strip()


def _extract_tagged_tool_use(text: str) -> str:
    return "\n\n".join(m.group(1).strip() for m in _TAGGED_TOOL_USE_RE.finditer(text) if m.group(1).strip())


def _strip_tagged_tool_use(text: str) -> str:
    return _TAGGED_TOOL_USE_RE.sub("", text).strip()


def _extract_reasoning_and_tool_use(entry: dict, action_raw) -> tuple[str, str]:
    if isinstance(action_raw, dict):
        raw_resp = _as_text(action_raw.get("raw_response"))
        text = raw_resp or _as_text(entry.get("response"))
        return _strip_tagged_tool_use(text), _extract_tagged_tool_use(text)

    parts: list[str] = []
    seen: set[str] = set()
    response = _as_text(entry.get("response"))
    _append_reasoning_section(parts, seen, "Model response", _clean_model_response(response))
    _append_reasoning_section(parts, seen, "Full plan", entry.get("full_plan"))
    _append_reasoning_section(parts, seen, "Executor plan", entry.get("executor_plan"))
    _append_reasoning_section(parts, seen, "Plan thoughts", entry.get("plan_thoughts"))
    _append_reasoning_section(parts, seen, "Planned action", entry.get("plan_code"))
    _append_reasoning_section(parts, seen, "Reflection", entry.get("reflection"))
    _append_reasoning_section(parts, seen, "Reflection thoughts", entry.get("reflection_thoughts"))
    return "\n\n".join(parts), _extract_xml_tool_use(response)


def load_trajectory(traj_dir: Path) -> dict:
    """
    Load a trajectory directory and return a normalized dict.

    Returns:
        {
            task_id: str,
            instruction: str,
            result_score: float | None,
            traj_dir: str,
            format: "claude" | "legacy",
            steps: [
                {
                    step_num: int,
                    action_code: str,
                    reasoning: str,
                    error: str,
                    reward: float,
                    done: bool,
                    action_type: str,
                    screenshot_path: Path | None,
                    action_input: dict,          # raw action.input from traj.jsonl ({} for legacy)
                }
            ],
            system_errors: [str],   # malformed / non-step JSONL lines
        }
    """
    traj_dir = traj_dir.resolve()
    if not traj_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {traj_dir}")

    # --- detect format ---
    claude_traj = traj_dir / "traj.jsonl"
    legacy_traj = traj_dir / "trajectory.jsonl"
    if _path_exists(claude_traj):
        fmt, jsonl_path = "claude", claude_traj
    elif _path_exists(legacy_traj):
        fmt, jsonl_path = "legacy", legacy_traj
    else:
        raise FileNotFoundError(
            f"No traj.jsonl or trajectory.jsonl found in {traj_dir}"
        )

    # --- task metadata ---
    instruction, task_id = "", traj_dir.name
    evaluator_func = ""

    # Try loading optional task metadata when a task_metadata tree
    # is present next to the repository.
    project_root = Path(__file__).resolve().parent.parent
    for domain_guess in [traj_dir.parent.name, ""]:
        config_path = project_root / "task_metadata" / "examples" / domain_guess / f"{task_id}.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            instruction = cfg.get("instruction", "")
            task_id = cfg.get("id", task_id)
            evaluator_func = cfg.get("evaluator", {}).get("func", "")
            break

    if fmt == "legacy" and not instruction:
        config_path = traj_dir / f"{task_id}.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            instruction = cfg.get("instruction", "")
            task_id = cfg.get("id", task_id)
            evaluator_func = cfg.get("evaluator", {}).get("func", "")

    # --- result score ---
    result_score = None
    result_txt = traj_dir / "result.txt"
    if _path_exists(result_txt):
        try:
            with open(_windows_long_path(result_txt), encoding="utf-8") as f:
                result_score = float(f.read().strip())
        except ValueError:
            pass

    # --- parse JSONL ---
    steps: list[dict] = []
    system_errors: list[str] = []

    with open(_windows_long_path(jsonl_path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not instruction:
                instruction = infer_instruction_from_entry(entry)

            # lines with an Error key but no step_num are system-level failures
            if "Error" in entry and "step_num" not in entry:
                system_errors.append(entry.get("Error", str(entry)))
                continue

            step_num = entry.get("step_num", 0)
            action_raw = entry.get("action", "")
            reasoning, llm_tool_use = _extract_reasoning_and_tool_use(entry, action_raw)
            error, action_type = "", "code"

            if fmt == "claude" and isinstance(action_raw, dict):
                action_type = action_raw.get("action_type", "tool_use")
                action_code = action_raw.get("command", "") or action_type
                action_input = action_raw.get("input", {})  # preserve for normalizer
            else:
                action_code = str(action_raw)
                action_input = {}

            info = entry.get("info", {})
            if isinstance(info, dict):
                error = info.get("error", "")

            # resolve screenshot path
            screenshot_path: Optional[Path] = None
            if fmt == "claude":
                sf = entry.get("screenshot_file", "")
                if sf:
                    p = traj_dir / sf
                    if _path_exists(p):
                        screenshot_path = p
            else:
                ts = entry.get("action_timestamp", "")
                p = traj_dir / "screenshots" / f"step_{step_num}_{ts}.png"
                if _path_exists(p):
                    screenshot_path = p

            steps.append(
                {
                    "step_num": step_num,
                    "action_code": action_code,
                    "reasoning": reasoning,
                    "llm_tool_use": llm_tool_use,
                    "error": error,
                    "reward": entry.get("reward", 0),
                    "done": entry.get("done", False),
                    "action_type": action_type,
                    "screenshot_path": screenshot_path,
                    "action_input": action_input,
                }
            )

    steps.sort(key=lambda s: s["step_num"])

    return {
        "task_id": task_id,
        "instruction": instruction,
        "result_score": result_score,
        "traj_dir": str(traj_dir),
        "format": fmt,
        "steps": steps,
        "system_errors": system_errors,
        "evaluator_func": evaluator_func,
    }


def load_normalized_trajectory(traj_dir: Path) -> dict:
    """Load a trajectory and normalize Anthropic coordinates to model-scale.

    Equivalent to ``normalize_trajectory(load_trajectory(traj_dir))``.
    Original files on disk are never modified.
    """
    from .trajectory_normalizer import normalize_trajectory
    raw = load_trajectory(traj_dir)
    return normalize_trajectory(raw)


def find_step(step_num: int, traj_data: dict) -> Optional[dict]:
    """Return the step dict for the given step_num, or None."""
    return next(
        (s for s in traj_data["steps"] if s["step_num"] == step_num), None
    )
