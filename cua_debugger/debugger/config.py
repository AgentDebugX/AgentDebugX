"""
Debugger configuration loader.

Configuration is read from ``debugger/config/debugger.json`` when present,
with safe defaults for optional trajectories under ``results/input_trajectory``. API keys
are never read from JSON config files; set provider-specific environment
variables such as ``OPENAI_API_KEY`` or ``ANTHROPIC_API_KEY`` instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_CONFIG_FILE = Path(__file__).parent / "config" / "debugger.json"

_DEFAULTS = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "base_urls": {},
    "trajectory_dir": "results/input_trajectory",
    "output_dir": "results/debugger_results",
    "workers": 1,
    "skip_existing": True,
    "trial_name": "claude-sonnet-4-5-20250929_50steps",
    "rca_thinking_budget": 16000,
    "rca_max_tokens": 16000,
    "rca_max_turns": 60,
    "agent_thinking_budget": 10000,
    "agent_max_tokens": 12000,
    "agent_max_turns": 60,
    "discuss_thinking_budget": 10000,
    "discuss_max_tokens": 12000,
    "discuss_max_tool_rounds": 15,
    "use_lessons": False,
    "lesson_top_k": 3,
    "lesson_db_folder": "",
    "embd_model": "text-embedding-3-small",
}

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "together": "zai-org/GLM-5",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
}


@dataclass
class DebuggerConfig:
    provider: str
    model: str
    trajectory_dir: Path
    output_dir: Path
    workers: int
    skip_existing: bool
    trial_name: Optional[str]
    base_urls: dict[str, str] = field(default_factory=dict)
    rca_thinking_budget: int = 16000
    rca_max_tokens: int = 16000
    rca_max_turns: int = 60
    agent_thinking_budget: int = 10000
    agent_max_tokens: int = 12000
    agent_max_turns: int = 60
    discuss_thinking_budget: int = 10000
    discuss_max_tokens: int = 12000
    discuss_max_tool_rounds: int = 15
    use_lessons: bool = False
    lesson_top_k: int = 3
    lesson_db_folder: str = ""
    embd_model: str = "text-embedding-3-small"

    @property
    def api_key_env(self) -> str:
        return {
            "anthropic": "ANTHROPIC_API_KEY",
            "together": "TOGETHER_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(self.provider, f"{self.provider.upper()}_API_KEY")


def load_config(config_file: Optional[Path] = None) -> DebuggerConfig:
    """Load debugger config from JSON, with safe defaults for missing fields."""
    path = config_file or _CONFIG_FILE
    data = dict(_DEFAULTS)

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        loaded.pop("api_keys", None)
        data.update(loaded)

    provider = str(data["provider"])
    model = str(data["model"] or _DEFAULT_MODELS.get(provider, ""))
    trajectory_dir = Path(data.get("trajectory_dir", data.get("results_dir", _DEFAULTS["trajectory_dir"])))
    output_dir = Path(data.get("output_dir", _DEFAULTS["output_dir"]))

    return DebuggerConfig(
        provider=provider,
        model=model,
        trajectory_dir=trajectory_dir,
        output_dir=output_dir,
        workers=int(data.get("workers", _DEFAULTS["workers"])),
        skip_existing=bool(data.get("skip_existing", _DEFAULTS["skip_existing"])),
        trial_name=data.get("trial_name", _DEFAULTS["trial_name"]),
        base_urls=dict(data.get("base_urls", _DEFAULTS["base_urls"]) or {}),
        rca_thinking_budget=int(data.get("rca_thinking_budget", _DEFAULTS["rca_thinking_budget"])),
        rca_max_tokens=int(data.get("rca_max_tokens", _DEFAULTS["rca_max_tokens"])),
        rca_max_turns=int(data.get("rca_max_turns", _DEFAULTS["rca_max_turns"])),
        agent_thinking_budget=int(data.get("agent_thinking_budget", _DEFAULTS["agent_thinking_budget"])),
        agent_max_tokens=int(data.get("agent_max_tokens", _DEFAULTS["agent_max_tokens"])),
        agent_max_turns=int(data.get("agent_max_turns", _DEFAULTS["agent_max_turns"])),
        discuss_thinking_budget=int(data.get("discuss_thinking_budget", _DEFAULTS["discuss_thinking_budget"])),
        discuss_max_tokens=int(data.get("discuss_max_tokens", _DEFAULTS["discuss_max_tokens"])),
        discuss_max_tool_rounds=int(data.get("discuss_max_tool_rounds", _DEFAULTS["discuss_max_tool_rounds"])),
        use_lessons=bool(data.get("use_lessons", _DEFAULTS["use_lessons"])),
        lesson_top_k=int(data.get("lesson_top_k", _DEFAULTS["lesson_top_k"])),
        lesson_db_folder=str(data.get("lesson_db_folder", _DEFAULTS["lesson_db_folder"])),
        embd_model=str(data.get("embd_model", _DEFAULTS["embd_model"])),
    )
