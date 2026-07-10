"""
Episodic Memory for OSWorld GUI Agent Debugger.

Stores trajectory records as JSON (no external DB required).
Storage file: debugger/memory/data/episodic.json

Record schema:
    {
        "id":            str   (UUID4),
        "type":          str   ("Type1" | "Type2" | "Type3"),
        "task_id":       str,
        "app_id":        str,
        "instruction":     str | None,
        "steps_count":   int,
        "terminal_step": int,
        "created_at":    str   (ISO-8601),
        "traj_path":     str   (absolute path to trajectory directory),
        "error_step":      int | None,
        "error_context":   dict | None,
        "agent_intention": str | None,
        "taxonomy_tag":    str | None,
        "annotation":      dict | None,
    }

Types:
    Type1 = failed trajectory
    Type2 = successful trajectory
    Type3 = debugged-success trajectory (failed → debugged → success)
"""

import os
import json
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone

_DATA_FILE = Path(__file__).parent / "data" / "episodic.json"


class Episode(BaseModel):
    """The prototype of Episode (raw -> summary -> lesson)"""
    id:                 uuid.UUID = Field(default_factory=lambda: uuid.uuid4())
    type:               Literal["Type1", "Type2", "Type3"]
    task_id:            str
    app_id:             str
    instruction:        str
    steps_count:        int
    terminal_step:      int
    traj_path:          str | os.PathLike
    error_step:         int | None
    error_context:      dict | None
    agent_intention:    str | None
    taxonomy_tag:       str | None
    annotation:         dict | None
    created_at:         str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __repr__(self):
        return (f"Episode(id={str(self.id).split('-')[0]}, "
                f"instruction={self.instruction[: 17] + '...' if len(self.instruction) > 20 else self.instruction})")

    def save(self, path: str | os.PathLike) -> None:
        path = Path(path)

        if path.is_dir():
            raise RuntimeError(f"[Episode.save]: Expected a file path, got a directory: {path}")

        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self.model_dump(mode="json"), fp, indent=4, ensure_ascii=False)


class TrajectoryType(str, Enum):
    TYPE1 = "Type1"  # failed trajectory
    TYPE2 = "Type2"  # successful trajectory
    TYPE3 = "Type3"  # debugged-success trajectory


class EpisodicMemory:
    """
    JSON-file-backed episodic memory store for GUI agent trajectories.

    All public methods acquire a file lock before reading or writing so that
    multiple processes (e.g. parallel workers) can share the same store safely.
    """

    def __init__(self, data_file: Optional[Path] = None) -> None:
        self._path = Path(data_file) if data_file else _DATA_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_db({"version": 1, "records": {}})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(
        self,
        trajectory: dict,
        traj_type: TrajectoryType,
        metadata: Optional[dict] = None,
        *,
        error_context: Optional[dict] = None,
        agent_intention: Optional[str] = None,
        taxonomy_tag: Optional[str] = None,
        error_step: Optional[int] = None,
        annotation: Optional[dict] = None,
    ) -> str:
        """
        Store a trajectory record and return its assigned id.

        Args:
            trajectory: dict returned by ``debugger.trajectory.load_trajectory()``.
                        Expected keys: task_id, traj_dir, steps (list).
            traj_type:  TrajectoryType.TYPE1 / TYPE2 / TYPE3.
            metadata:   Optional extra fields (e.g. app_id if not in trajectory).

        Keyword-only Step 1 (EC_t) fields — all default to None for backward
        compatibility with existing call sites:
            error_context:   EC_t window dict from build_error_context()
            agent_intention: short summary from extract_intention()
            taxonomy_tag:    taxonomy tag from RCA (e.g. "S2")
            error_step:      step_num that the error occurred at
            annotation:      normalized annotation dict (human or llm)

        Returns:
            The UUID string assigned to the new record.
        """
        meta = metadata or {}
        steps = trajectory.get("steps", [])
        steps_count = len(steps)

        # terminal_step: the step_num of the last step, or steps_count - 1
        if steps:
            terminal_step = steps[-1].get("step_num", steps_count - 1)
        else:
            terminal_step = 0

        record_id = str(uuid.uuid4())
        record = {
            "id": record_id,
            "type": str(traj_type.value) if isinstance(traj_type, TrajectoryType) else str(traj_type),
            "task_id": trajectory.get("task_id") or meta.get("task_id", ""),
            "app_id": meta.get("app_id") or trajectory.get("app_id", ""),
            "instruction": trajectory.get("instruction"),
            "steps_count": steps_count,
            "terminal_step": terminal_step,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "traj_path": trajectory.get("traj_dir", ""),
            # ── Step 1 (EC_t) additions ──
            "error_step": error_step,
            "error_context": error_context,
            "agent_intention": agent_intention,
            "taxonomy_tag": taxonomy_tag,
            "annotation": annotation,
        }

        db = self._read_db()
        db["records"][record_id] = record
        self._write_db(db)
        return record_id

    def read(self, record_id: str) -> Optional[dict]:
        """
        Return the record with the given id, or None if not found.
        """
        db = self._read_db()
        return db["records"].get(record_id)

    def list(self, filter: Optional[dict] = None) -> list[dict]:
        """
        Return all records matching the filter criteria.

        Supported filter keys: type, task_id, app_id.
        An empty or None filter returns all records.

        Example::
            mem.list({"type": "Type1"})
            mem.list({"app_id": "chrome", "type": "Type3"})
        """
        db = self._read_db()
        records = list(db["records"].values())
        if not filter:
            return records

        result = []
        for rec in records:
            if all(rec.get(k) == v for k, v in filter.items()):
                result.append(rec)
        return result

    def delete(self, record_id: str) -> bool:
        """
        Delete the record with the given id.

        Returns True if deleted, False if not found.
        """
        db = self._read_db()
        if record_id not in db["records"]:
            return False
        del db["records"][record_id]
        self._write_db(db)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lock(fh, exclusive: bool = False) -> None:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)

    @staticmethod
    def _unlock(fh) -> None:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_UN)

    def _read_db(self) -> dict:
        with open(self._path, "r", encoding="utf-8") as fh:
            self._lock(fh, exclusive=False)
            try:
                return json.load(fh)
            finally:
                self._unlock(fh)

    def _write_db(self, db: dict) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            self._lock(fh, exclusive=True)
            try:
                json.dump(db, fh, indent=2, ensure_ascii=False)
            finally:
                self._unlock(fh)
