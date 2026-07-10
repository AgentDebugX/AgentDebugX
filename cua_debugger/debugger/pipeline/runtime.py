"""Runtime helpers: shared logger, trial directory, file logging."""

import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("debugger_pipeline")


def create_trial_dir(output_dir: Path, trial_name: str, debugger_model: str,
                     trail_name: str | None = None) -> Path:
    """Create <output_dir>/<trial_name>/<safe_model>[-<trail_name>]/rca/ and return the debugger dir.

    `trail_name` lets multiple runs of the same model coexist (e.g. "v2"
    for prompt iterations). When set, "-{trail_name}" is appended to the model name.
    """
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", debugger_model)
    if trail_name:
        safe_model = f"{safe_model}-{re.sub(r'[^A-Za-z0-9._-]', '-', trail_name)}"
    trial_dir = output_dir / trial_name / safe_model
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "rca").mkdir(exist_ok=True)
    return trial_dir


def setup_logging(trial_dir: Path) -> Path:
    """Set up logging to stdout + a log file inside trial_dir."""
    log_file = trial_dir / "debugger.log"
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log_file
