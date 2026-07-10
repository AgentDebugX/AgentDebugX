"""DeepDebug-only memory backends and prompt rendering."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from agentdebug.core.llm import EmbeddingClient
from agentdebug.core.models import AgentTrajectory, DiagnosticReport, model_to_json, utc_now

_TOKEN_RE = re.compile(r'[A-Za-z0-9_]+')


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _similarity(a: str, b: str) -> float:
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    if union == 0:
        return 0.0
    return inter / union


def _trajectory_text(trajectory: AgentTrajectory, max_events: int = 80) -> str:
    lines: List[str] = []
    for i, e in enumerate(trajectory.events[:max_events]):
        lines.append(
            f'Step {i} - {e.agent_name}: '
            f'input={str(e.input)} output={str(e.output)} error={str(e.error)}'
        )
    return '\n'.join(lines)


@dataclass
class MemoryReference:
    trajectory_text: str
    description: str
    evidence: List[str]
    suggestion: str
    score: float


class DeepMemoryStore(Protocol):
    def search_references(
        self, trajectory: AgentTrajectory, top_k: int = 3
    ) -> Optional[List[MemoryReference]]:
        ...

    def save_run(self, trajectory: AgentTrajectory, report: DiagnosticReport) -> None:
        ...


class NullMemoryStore:
    """No-op memory store: stores nothing, retrieves nothing.

    Used to run DeepDebug / evaluations without any cross-run memory (clean,
    reproducible, no SQLite side effects)."""

    def search_references(
        self, trajectory: AgentTrajectory, top_k: int = 3
    ) -> Optional[List[MemoryReference]]:
        return []

    def save_run(self, trajectory: AgentTrajectory, report: DiagnosticReport) -> None:
        return None


class SQLiteDeepMemoryStore:
    """DeepDebug-only memory store with episodic/semantic/procedural tables."""

    def __init__(
        self,
        path: str = '.agentdebug/deep_memory.sqlite',
        embedder: Optional[EmbeddingClient] = None,
        backfill_missing_embeddings: bool = True,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.backfill_missing_embeddings = backfill_missing_embeddings
        self._init_db()

    def search_references(
        self, trajectory: AgentTrajectory, top_k: int = 3
    ) -> Optional[List[MemoryReference]]:
        query = _trajectory_text(trajectory)
        query_vec: Optional[List[float]] = None
        if self.embedder is not None:
            try:
                vectors = self.embedder.embed([query])
                if vectors:
                    query_vec = vectors[0]
            except Exception:
                query_vec = None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.episode_id, e.trajectory_text, e.retrieval_text,
                       s.description, p.evidence_json, p.suggestion, e.embedding_json
                FROM episodic_memories e
                LEFT JOIN semantic_memories s ON s.episode_id = e.episode_id
                LEFT JOIN procedural_memories p ON p.episode_id = e.episode_id
                ORDER BY e.created_at DESC
                LIMIT 200
                """
            ).fetchall()
        scored: List[MemoryReference] = []
        pending_backfill: List[tuple[int, str]] = []
        backfilled_vectors: dict[int, List[float]] = {}
        if query_vec is not None and self.backfill_missing_embeddings and self.embedder is not None:
            for row in rows:
                episode_id = int(row[0])
                retrieval_text = str(row[2] or '')
                db_vec = self._parse_embedding(str(row[6] or ''))
                if db_vec is None and retrieval_text:
                    pending_backfill.append((episode_id, retrieval_text))
            if pending_backfill:
                backfilled_vectors = self._backfill_embeddings(pending_backfill)

        for row in rows:
            episode_id = int(row[0])
            retrieval_text = str(row[2] or '')
            score = 0.0
            if query_vec is not None:
                db_vec = self._parse_embedding(str(row[6] or ''))
                if db_vec is None:
                    db_vec = backfilled_vectors.get(episode_id)
                if db_vec is not None:
                    score = self._cosine(query_vec, db_vec)
            if score <= 0.0:
                score = _similarity(query, retrieval_text)
            if score <= 0.0:
                continue
            evidence_raw = str(row[4] or '[]')
            evidence = self._parse_evidence(evidence_raw)
            scored.append(
                MemoryReference(
                    trajectory_text=str(row[1] or ''),
                    description=str(row[3] or ''),
                    evidence=evidence,
                    suggestion=str(row[5] or ''),
                    score=score,
                )
            )
        if not scored:
            return None
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:max(1, top_k)]

    def save_run(self, trajectory: AgentTrajectory, report: DiagnosticReport) -> None:
        trajectory_json = model_to_json(trajectory)
        trajectory_text = _trajectory_text(trajectory, max_events=200)
        retrieval_text = (
            f'goal={trajectory.goal or ""}\nframework={trajectory.framework or ""}\n'
            f'{trajectory_text}'
        )
        embedding_json = ''
        if self.embedder is not None:
            try:
                vectors = self.embedder.embed([retrieval_text])
                if vectors:
                    embedding_json = json.dumps(vectors[0], ensure_ascii=False)
            except Exception:
                embedding_json = ''
        created = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO episodic_memories(
                    trace_id, goal, framework, trajectory_json,
                    trajectory_text, retrieval_text, embedding_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory.trace_id,
                    trajectory.goal,
                    trajectory.framework,
                    trajectory_json,
                    trajectory_text,
                    retrieval_text,
                    embedding_json,
                    created,
                ),
            )
            row = conn.execute('SELECT last_insert_rowid()').fetchone()
            episode_id = int(row[0]) if row else 0

            descriptions = [f.failure_mode.description for f in report.findings]
            description_text = ' | '.join(d for d in descriptions if d).strip()
            if not description_text:
                description_text = report.summary
            conn.execute(
                """
                INSERT INTO semantic_memories(
                    episode_id, description, summary, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    episode_id,
                    description_text,
                    report.summary,
                    created,
                ),
            )

            evidences: List[str] = []
            suggestions: List[str] = []
            for finding in report.findings:
                evidences.extend([str(e) for e in finding.evidence])
                if finding.suggestion:
                    suggestions.append(str(finding.suggestion))
            if not suggestions and report.suggestions:
                suggestions = [str(s) for s in report.suggestions]
            suggestion_text = ' | '.join(dict.fromkeys(suggestions))
            conn.execute(
                """
                INSERT INTO procedural_memories(
                    episode_id, suggestion, evidence_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    episode_id,
                    suggestion_text,
                    self._dump_evidence(evidences),
                    created,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memories (
                    episode_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    goal TEXT,
                    framework TEXT,
                    trajectory_json TEXT NOT NULL,
                    trajectory_text TEXT NOT NULL,
                    retrieval_text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            try:
                conn.execute(
                    "ALTER TABLE episodic_memories ADD COLUMN embedding_json TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_memories (
                    semantic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS procedural_memories (
                    procedural_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL,
                    suggestion TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _dump_evidence(items: List[str]) -> str:
        escaped = [i.replace('\\', '\\\\').replace('"', '\\"') for i in items]
        inner = ','.join(f'"{x}"' for x in escaped)
        return f'[{inner}]'

    @staticmethod
    def _parse_evidence(raw: str) -> List[str]:
        txt = raw.strip()
        if not (txt.startswith('[') and txt.endswith(']')):
            return [txt] if txt else []
        body = txt[1:-1].strip()
        if not body:
            return []
        parts = [p.strip() for p in body.split('","')]
        out: List[str] = []
        for p in parts:
            p = p.strip('"')
            p = p.replace('\\"', '"').replace('\\\\', '\\')
            if p:
                out.append(p)
        return out

    @staticmethod
    def _parse_embedding(raw: str) -> Optional[List[float]]:
        txt = raw.strip()
        if not txt:
            return None
        try:
            arr = json.loads(txt)
        except json.JSONDecodeError:
            return None
        if not isinstance(arr, list):
            return None
        try:
            return [float(v) for v in arr]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    def _backfill_embeddings(self, items: List[tuple[int, str]]) -> dict[int, List[float]]:
        if self.embedder is None or not items:
            return {}
        episode_ids = [eid for eid, _ in items]
        texts = [txt for _, txt in items]
        try:
            vectors = self.embedder.embed(texts)
        except Exception:
            return {}
        if len(vectors) != len(items):
            return {}
        out: dict[int, List[float]] = {}
        with self._connect() as conn:
            for episode_id, vec in zip(episode_ids, vectors):
                payload = json.dumps(vec, ensure_ascii=False)
                conn.execute(
                    """
                    UPDATE episodic_memories
                    SET embedding_json = ?
                    WHERE episode_id = ? AND (embedding_json = '' OR embedding_json IS NULL)
                    """,
                    (payload, episode_id),
                )
                out[episode_id] = vec
        return out


def render_reference_prompt(references: Optional[List[MemoryReference]]) -> Optional[str]:
    if not references:
        return None
    chunks: List[str] = []
    for ref in references:
        evidence_text = '\n'.join(ref.evidence) if ref.evidence else ''
        chunks.append(
            'Historical Reference Case\n'
            f'- Prior Trajectory Summary:\n{ref.trajectory_text}\n'
            f'- Root Cause Description:\n{ref.description}\n'
            f'- Key Evidence:\n{evidence_text}\n'
            f'- Recommended Fix Pattern:\n{ref.suggestion}\n'
        )
    return '\n'.join(chunks)


def import_hub_bundles(store: DeepMemoryStore, path) -> int:
    """Import Error Hub bundle(s) into a deep-memory store (design gap G2).

    ``path`` may be a single bundle directory (contains ``manifest.json``) or a
    directory of bundle directories (e.g. a pulled hub corpus). Each bundle
    that carries a diagnostic report with findings is written into ``store``
    via ``save_run``, so accepted team/community cases seed future DeepDebug
    retrievals exactly like locally diagnosed runs. Bundles without a report
    are skipped (nothing to learn from). Returns the number imported.
    """
    import logging
    from pathlib import Path as _Path
    # Lazy import: hub lives under diagnose.actions and importing it eagerly
    # here would create a package cycle at module-import time.
    from agentdebug.diagnose.actions.hub.bundle import unpack_bundle

    log = logging.getLogger('agentdebug.deep_memory')
    root = _Path(path)
    if (root / 'manifest.json').exists():
        candidates = [root]
    else:
        # A pulled corpus may nest bundles (pack_bundle writes
        # out_dir/<bundle_id>/); recurse so any layout imports.
        candidates = sorted(p.parent for p in root.rglob('manifest.json'))
    imported = 0
    for bundle_dir in candidates:
        try:
            bundle = unpack_bundle(bundle_dir)
        except Exception as exc:
            log.warning('skipping unreadable bundle %s: %s', bundle_dir, exc)
            continue
        report = getattr(bundle, 'report', None)
        if report is None or not getattr(report, 'findings', None):
            continue
        store.save_run(bundle.trajectory, report)
        imported += 1
    return imported


__all__ = [
    'DeepMemoryStore',
    'MemoryReference',
    'NullMemoryStore',
    'SQLiteDeepMemoryStore',
    'import_hub_bundles',
    'render_reference_prompt',
]
