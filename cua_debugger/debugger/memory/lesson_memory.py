import os
import json
import uuid
from pathlib import Path
from typing import Any, List, Callable, Tuple, TypeVar, Literal
from pydantic import BaseModel, Field, model_validator
from datetime import datetime, timezone

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma

from debugger.utils import ABlockLogger, RWLock
from debugger.taxonomy import SUBTYPE_DEFINITIONS


class Lesson(BaseModel):
    """The prototype of Lesson (raw -> summary -> lesson)"""
    id:                     uuid.UUID = Field(default_factory=lambda: uuid.uuid4())
    title:                  str
    distilled_lesson:       str
    trigger_condition:      str
    taxonomy_tag:           str
    failed_action:          str
    corrected_action:       str
    distinguishing_feature: str
    evidence:               str
    confusion_set:          list[str] = Field(default_factory=list)
    app_id:                 str | None = None
    episodic_refs:          list[str] = Field(default_factory=list)
    created_at:             str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def _drop_self_tag_from_confusion_set(self) -> "Lesson":
        """Ensure ``taxonomy_tag`` never appears in ``confusion_set``.

        The invariant "a lesson is not confused with itself" can be violated
        by LLM non-determinism in ``distill_from_annotation`` or by the
        set-union step inside any merge rule.  Stripping the self-tag here
        in a single ``mode="after"`` validator is the defensive choke point:
        every construction path (``__init__``, ``model_validate``,
        ``Lesson(**merged_dict)``) is automatically covered.  Original list
        order is preserved.
        """
        if self.taxonomy_tag and self.taxonomy_tag in self.confusion_set:
            self.confusion_set = [
                tag for tag in self.confusion_set if tag != self.taxonomy_tag
            ]
        return self

    def __repr__(self) -> str:
        return (f"Lesson(id={str(self.id).split('-')[0]}, "
                f"title={self.title[: 17] + '...' if len(self.title) > 20 else self.title})")

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")

    def to_json(self) -> dict:
        return self.model_dump(mode="json")

    def to_prompt(self) -> str:
        correct_tag = f"{self.taxonomy_tag}({SUBTYPE_DEFINITIONS.get(self.taxonomy_tag, '<|NO-DEFINITION|>')})"

        confusion = ""
        if self.confusion_set:
            confusion_tags = [
                f"{tag}({SUBTYPE_DEFINITIONS.get(tag, '<|NO-DEFINITION|>')})"
                for tag in self.confusion_set
            ]
            confusion_tags = ", ".join(confusion_tags)

            confusion = (
                f" This is classified as **{correct_tag}** rather than {confusion_tags} "
                f"because {self.distinguishing_feature}."
            )

        return (
            f"## {self.title}\n\n"
            f"**Related App:** {self.app_id or 'unknown'}\n\n"
            f"**Trigger:** {self.trigger_condition}\n\n"
            f"**Worked example:** "
            f"In this scenario, the agent attempted to {self.failed_action}. "
            f"By examining the trajectory, the debugger noticed that {self.evidence} "
            f"This identifies the error as **{correct_tag}**.{confusion}\n\n"
            f"**Lesson:** {self.distilled_lesson}\n\n"
            # f"**Recovery:** At the identified error step, replace the failed action with: "
            # f"{self.corrected_action}"
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        suffix = path.suffix.lower()
        if suffix in (".txt", ".md"):
            path.write_text(self.to_prompt(), encoding="utf-8")
        elif suffix == ".json":
            path.write_text(
                json.dumps(self.to_json(), indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            raise NotImplementedError(f"save() does not support extension '{suffix}'")



class MergeRule:
    """Base class for merge detection rules."""

    def __call__(self, lesson: Lesson) -> List[uuid.UUID]:
        raise NotImplementedError

    def merge_func(self, bucket: List[Lesson]) -> List[Lesson]:
        raise NotImplementedError


class MapMergeRule(MergeRule):
    def __init__(
        self,
        app_list:  List[str],
        tag_list:  List[str],
        threshold: int = 1,
        api_key:   None | str = None,
        base_url:  None | str = None,
        model:     None | str = None,
    ):
        # the map [taxonomy][app_id] storing ids queued for merging
        self.map = {t: {a: [] for a in app_list} for t in tag_list}

        self.llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

        self.threshold = threshold

    def __call__(self, lesson: Lesson, threshold: None | int = None) -> List[uuid.UUID]:
        tag = lesson.taxonomy_tag
        app = lesson.app_id
        thr = (threshold or self.threshold)
        assert thr is not None and thr > 0

        if tag not in self.map or app not in self.map[tag]:
            return []

        self.map[tag][app].append(str(lesson.id))

        if len(self.map[tag][app]) <= thr:
            return []

        # flush the bucket and hand its ids to the caller
        ids = [uuid.UUID(i) for i in self.map[tag][app]]
        self.map[tag][app] = []
        return ids

    @staticmethod
    def _format_input(bucket: List[Lesson]) -> str:
        """Serialize lessons for the prompt, dropping auto-generated and auto-filled fields."""
        items = []
        for l in bucket:
            d = l.to_json()
            for k in ("id", "created_at", "episodic_refs"):
                d.pop(k, None)
            items.append(d)
        return "\n".join(json.dumps(it, ensure_ascii=False, indent=2) for it in items)

    @staticmethod
    def _build_prompt_single(payload: str) -> str:
        taxonomy = json.dumps(SUBTYPE_DEFINITIONS, ensure_ascii=False, indent=2)
        return (
            "# Lesson Consolidation (Single-Cluster Mode)\n\n"
            "## Purpose\n"
            "You are given a bucket of agent debugging lessons that all share the same "
            "`taxonomy_tag` (error type) and the same `app_id` (task type). Produce ONE "
            "new distilled lesson that captures the generalizable pattern shared across "
            "the whole bucket.\n\n"
            "## Taxonomy Reference\n"
            "Codes used in `taxonomy_tag` and `confusion_set` are drawn from this dictionary. "
            "Use the definitions when writing `distinguishing_feature` (which compares the "
            "chosen tag against similar codes in the confusion set):\n"
            f"```json\n{taxonomy}\n```\n\n"
            "## Workflow\n\n"
            "### Step 1 — Identify the shared pattern\n"
            "Scan the bucket for what is common across all lessons:\n"
            "- the situation that precedes the failure\n"
            "- the category of incorrect action taken\n"
            "- the kind of evidence that exposes the error\n\n"
            "### Step 2 — Strip scenario-specific noise\n"
            "Remove concrete object ids, one-off UI strings, particular filenames, URLs, "
            "step numbers, screenshot references, and any details that would not transfer "
            "to a future occurrence of this error type on this task type.\n\n"
            "### Step 3 — Fill the merged fields\n"
            "Produce exactly these seven fields, phrased generally enough that a future "
            "agent facing a similar situation could recognize and apply them:\n"
            "- `title` — short generalizable name of the pattern\n"
            "- `distilled_lesson` — the rule/insight a future agent should apply\n"
            "- `trigger_condition` — when this pattern applies\n"
            "- `failed_action` — the category of action that fails here\n"
            "- `corrected_action` — what to do instead\n"
            "- `distinguishing_feature` — why this is the chosen `taxonomy_tag` rather "
            "than a similar code in the confusion set (reference the taxonomy above)\n"
            "- `evidence` — the kind of observation that confirms this error\n\n"
            "### Step 4 — Do NOT produce these fields\n"
            "These are auto-filled by post-processing. Omit them entirely from your output:\n"
            "- `taxonomy_tag` — inherited from the first input lesson\n"
            "- `app_id` — inherited from the first input lesson\n"
            "- `confusion_set` — set-union across all input lessons\n"
            "- `episodic_refs` — set-union across all input lessons\n\n"
            "## Example\n"
            "Input (3 lessons, all `taxonomy_tag=G1`, `app_id=web_shopping`):\n"
            "- failed_action: 'clicked the Add-to-Cart button at (420, 330)'\n"
            "- failed_action: 'tapped the blue Checkout icon near the header'\n"
            "- failed_action: 'selected the Confirm Purchase button in modal #checkout-modal'\n\n"
            "Merged output `failed_action`:\n"
            "- 'issued a click on a visually similar but semantically different commerce-action "
            "button, overshooting the intended target'\n\n"
            "Concrete coordinates, colors, and DOM ids are dropped; the transferable "
            "category of the mistake is kept.\n\n"
            "## Input Lessons\n"
            f"```json\n{payload}\n```\n\n"
            "## Output Format\n"
            "Return ONLY a single JSON object (no markdown fences, no prose, no trailing "
            "text, no explanation, no reasoning) matching exactly this schema:\n"
            "{\n"
            '    "title": <str>,\n'
            '    "distilled_lesson": <str>,\n'
            '    "trigger_condition": <str>,\n'
            '    "failed_action": <str>,\n'
            '    "corrected_action": <str>,\n'
            '    "distinguishing_feature": <str>,\n'
            '    "evidence": <str>\n'
            "}"
        )

    @staticmethod
    def _build_prompt_array(payload: str) -> str:
        taxonomy = json.dumps(SUBTYPE_DEFINITIONS, ensure_ascii=False, indent=2)
        return (
            "# Lesson Consolidation (Multi-Cluster Mode)\n\n"
            "## Purpose\n"
            "You are given a bucket of agent debugging lessons that all share the same "
            "`taxonomy_tag` (error type) and the same `app_id` (task type). The bucket "
            "may contain several distinct underlying failure modes mixed together. "
            "Cluster the lessons by underlying failure mode and produce ONE new distilled "
            "lesson per cluster.\n\n"
            "## Taxonomy Reference\n"
            "Codes used in `taxonomy_tag` and `confusion_set` are drawn from this dictionary. "
            "Use the definitions when writing `distinguishing_feature`:\n"
            f"```json\n{taxonomy}\n```\n\n"
            "## Workflow\n\n"
            "### Step 1 — Cluster by failure mode\n"
            "Two lessons belong to the same cluster when they share the same triggering "
            "situation, the same category of failed action, and the same kind of "
            "corrective action. Distinct failure modes must stay in separate clusters.\n\n"
            "### Step 2 — For each cluster, identify its shared pattern\n"
            "Within a cluster, find what is common across its members:\n"
            "- the situation that precedes the failure\n"
            "- the category of incorrect action taken\n"
            "- the kind of evidence that exposes the error\n\n"
            "### Step 3 — Strip scenario-specific noise\n"
            "Remove concrete object ids, one-off UI strings, particular filenames, URLs, "
            "step numbers, screenshot references, and any details that would not transfer "
            "to a future occurrence.\n\n"
            "### Step 4 — Fill the merged fields per cluster\n"
            "For each cluster, produce exactly these seven fields, phrased generally "
            "enough that a future agent could recognize and apply them:\n"
            "- `title` — short generalizable name of the pattern\n"
            "- `distilled_lesson` — the rule/insight a future agent should apply\n"
            "- `trigger_condition` — when this pattern applies\n"
            "- `failed_action` — the category of action that fails here\n"
            "- `corrected_action` — what to do instead\n"
            "- `distinguishing_feature` — why this is the chosen `taxonomy_tag` rather "
            "than a similar code in the confusion set (reference the taxonomy above)\n"
            "- `evidence` — the kind of observation that confirms this error\n\n"
            "### Step 5 — Do NOT produce these fields\n"
            "These are auto-filled by post-processing. Omit them entirely from your output:\n"
            "- `taxonomy_tag` — inherited from the first input lesson\n"
            "- `app_id` — inherited from the first input lesson\n"
            "- `confusion_set` — set-union across all input lessons\n"
            "- `episodic_refs` — set-union across all input lessons\n\n"
            "### Constraint\n"
            "The output array must contain fewer entries than the input. Merging is the "
            "point; do not echo the input one-to-one.\n\n"
            "## Example\n"
            "Input (5 lessons, all `taxonomy_tag=R7`, `app_id=file_manager`):\n"
            "- 3 lessons about passing a path string where a file handle was expected\n"
            "- 2 lessons about passing the wrong file-mode flag to an open call\n\n"
            "Merged output (2 clusters):\n"
            "1. 'Argument-type mismatch: supplies a path-like object where the API "
            "requires an opened handle'\n"
            "2. 'Argument-value mismatch: supplies an incorrect mode flag that conflicts "
            "with the intended read/write operation'\n\n"
            "## Input Lessons\n"
            f"```json\n{payload}\n```\n\n"
            "## Output Format\n"
            "Return ONLY a JSON array (no markdown fences, no prose, no trailing text, "
            "no explanation, no reasoning) where each element matches exactly this schema:\n"
            "[\n"
            "    {\n"
            '        "title": <str>,\n'
            '        "distilled_lesson": <str>,\n'
            '        "trigger_condition": <str>,\n'
            '        "failed_action": <str>,\n'
            '        "corrected_action": <str>,\n'
            '        "distinguishing_feature": <str>,\n'
            '        "evidence": <str>\n'
            "    },\n"
            "    ...\n"
            "]"
        )

    @staticmethod
    def _strip_fence(content: str) -> str:
        """Strip markdown code fences if present."""
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.rsplit("```", 1)[0]
        return content.strip()

    def _parse_single(self, content: str) -> List[dict]:
        """Parse a single JSON object into a one-element dict list."""
        return [json.loads(self._strip_fence(content))]

    def _parse_array(self, content: str) -> List[dict]:
        """Parse a JSON array into a list of dicts."""
        return json.loads(self._strip_fence(content))

    def merge_func(self, bucket: List[Lesson]) -> List[Lesson]:
        if not bucket:
            return []

        payload = self._format_input(bucket)

        if self.threshold == 1:
            prompt = self._build_prompt_single(payload)
        else:
            prompt = self._build_prompt_array(payload)

        response = self.llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        if self.threshold == 1:
            raw = self._parse_single(content)
        else:
            raw = self._parse_array(content)

        # auto-fill fields the LLM did not produce
        tag = bucket[0].taxonomy_tag
        app = bucket[0].app_id
        confusion = sorted({c for l in bucket for c in l.confusion_set})
        refs = sorted({r for l in bucket for r in l.episodic_refs})

        merged = []
        for item in raw:
            item["taxonomy_tag"] = tag
            item["app_id"] = app
            item["confusion_set"] = confusion
            item["episodic_refs"] = refs
            merged.append(Lesson(**item))

        return merged


T = TypeVar("T")
class LessonMemory:
    def __init__(
        self,
        api_key:    None | str = None,
        base_url:   None | str = None,
        model:      None | str = None,
        db_folder:  str | os.PathLike = Path("./lesson_database"),
        log_level:  Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
        log_file:   None | str | os.PathLike = None
    ) -> None:
        self.db_path = Path(db_folder)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.merge_rules = []

        self.model = OpenAIEmbeddings(
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

        self.database = Chroma(
            collection_name=self.db_path.name,
            embedding_function=self.model,
            persist_directory=str(self.db_path),
            collection_metadata={"hnsw:space": "cosine"}
        )

        self._rw_lock = RWLock()

        self.logger = ABlockLogger(level=log_level)
        self.logger.set_role(role="LessonMemory")
        if log_file:
            self.logger.bind_file(log_file, mode='a')

    def _lock(self, *, exclusive: bool = False) -> None:
        """Acquire shared (read) or exclusive (write) lock."""
        if exclusive:
            self._rw_lock.acquire_write()
        else:
            self._rw_lock.acquire_read()

    def _unlock(self, *, exclusive: bool = False) -> None:
        """Release shared (read) or exclusive (write) lock."""
        if exclusive:
            self._rw_lock.release_write()
        else:
            self._rw_lock.release_read()

    def _read(self, fn: Callable[[], T]) -> T:
        """Execute fn under shared lock."""
        self._lock(exclusive=False)
        try:
            return fn()
        finally:
            self._unlock(exclusive=False)

    def _write(self, fn: Callable[[], T]) -> T:
        """Execute fn under exclusive lock."""
        self._lock(exclusive=True)
        try:
            return fn()
        finally:
            self._unlock(exclusive=True)

    def __len__(self) -> int:
        return self._read(lambda: self.database._collection.count())

    def __iter__(self):
        entries = self._read(lambda: self.database._collection.get())
        for i, doc_id in enumerate(entries["ids"]):
            meta = entries["metadatas"][i]
            yield Lesson(**meta)

    def _add(self, node: Lesson) -> None:
        meta = node.to_json()
        # Chroma rejects empty lists in metadata
        for k in ("confusion_set", "episodic_refs"):
            if meta.get(k) == []:
                meta.pop(k)
        entry = Document(
            page_content=self._assemble_retrieval_text(node),
            metadata=meta
        )
        self.database.add_documents([entry], ids=[str(node.id)])

    def _pop(self, node_id: uuid.UUID) -> None | Lesson:
        """Delete and return a lesson. Caller must hold the write lock."""
        existing = self.database._collection.get(ids=[str(node_id)])
        if not existing["ids"]:
            return None

        self.database.delete(ids=[str(node_id)])
        self.logger.debug(message=f"entry@{node_id} popped")
        return Lesson(**existing["metadatas"][0])

    @staticmethod
    def _assemble_retrieval_text(node: Lesson) -> str:
        """Build the text that actually gets embedded.

        Failure-pattern-oriented composition: the embedding axis is "what
        went wrong + what the right approach would have been + how to
        recognise this class of error", NOT lesson title / app / trigger
        context.  Pairs cleanly with the failure-pattern query the
        ``run_rag`` pipeline derives from ``extract_intention``.

        Notes:
          * ``app_id`` is intentionally OMITTED so the embedding rewards
            cross-app failure-pattern matches; the run_rag layer reranks
            by ``app_id`` separately when it cares.
          * ``taxonomy_tag`` is kept as a short categorical marker — the
            merge router's bucket filter uses metadata not embedding, but
            keeping the tag here helps the query/lesson alignment when
            both sides describe an error of a known type.
        """
        parts = [
            f"[TAG] {node.taxonomy_tag}",
            f"[FAILED ACTION] {node.failed_action}",
            f"[OBSERVED EVIDENCE] {node.evidence}",
            f"[CORRECT APPROACH] {node.corrected_action}",
            f"[KEY DIFFERENTIATOR] {node.distinguishing_feature}",
            f"[LESSON] {node.distilled_lesson}",
        ]
        return "\n".join(parts)

    def add(self, node: Lesson, max_retries: int = 3) -> None:
        def _execute():
            # initial insert with retry
            for attempt in range(1, max_retries + 1):
                try:
                    self._add(node)
                    self.logger.debug(message=f"entry@{node.id} added")
                    break
                except Exception as e:
                    if attempt == max_retries:
                        self.logger.warning(message=f"entry@{node.id} failed to add: {e}")
                        raise

            # fire every rule, pop flushed buckets, insert merged lessons
            for rule in self.merge_rules:
                ids = rule(node)
                if not ids:
                    continue

                bucket = list(filter(None, [self._pop(i) for i in ids]))
                new_lessons = rule.merge_func(bucket)

                added_new_lessons = []
                for nl in new_lessons:
                    for attempt in range(1, max_retries + 1):
                        try:
                            self._add(nl)
                            added_new_lessons.append(str(nl.id).split('-')[0])
                            break
                        except Exception as e:
                            if attempt == max_retries:
                                self.logger.warning(message=f"entry@{nl.id} failed to add during merge: {e}")
                                raise

                self.logger.debug(message=f"entry@{[str(ol.id).split('-')[0] for ol in bucket]} -> {added_new_lessons} merged")

        self._write(_execute)

    def pop(self, node_id: uuid.UUID) -> None | Lesson:
        result = self._write(lambda: self._pop(node_id))
        return result

    def get(self, node_id: uuid.UUID) -> Lesson:
        entry = self._read(lambda: self.database._collection.get(ids=[str(node_id)]))
        if not entry["ids"]:
            raise KeyError(f"node {node_id} not found")

        meta = entry["metadatas"][0]

        self.logger.debug(message=f"entry@{node_id} get")
        return Lesson(**meta)

    def delete(self, node_id: uuid.UUID) -> None:
        self._write(lambda: self.database.delete(ids=[str(node_id)]))
        self.logger.debug(message=f"entry@{node_id} deleted")

    def update(self, node_id: uuid.UUID, node: Lesson) -> None:
        def _do_update():
            existing = self.database._collection.get(ids=[str(node_id)])
            if not existing["ids"]:
                raise KeyError(f"node {node_id} not found")
            self.database.delete(ids=[str(node_id)])
            entry = Document(page_content=self._assemble_retrieval_text(node), metadata=node.to_json())
            self.database.add_documents([entry], ids=[str(node_id)])

        self._write(_do_update)
        self.logger.debug(message=f"entry@{node_id} updated")

    def clear(self) -> None:
        def _do_clear():
            ids = self.database._collection.get()["ids"]
            if ids:
                self.database.delete(ids=ids)

        self._write(_do_clear)
        self.logger.warning(message=f"LessonRAG database cleared")

    def dump(self, dir: str | os.PathLike, suffix: str="json") -> None:
        def _do_dump():
            for lesson in self:
                path = Path(dir) / f"{lesson.id}.{suffix}"
                lesson.save(path)

        self._read(_do_dump)
        self.logger.info(message=f"LessonRAG database saved")


    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        score_threshold: float = 0.0,
        max_retries: int = 3,
    ) -> List[Lesson]:
        scored = self.retrieve_with_scores(
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            max_retries=max_retries,
        )
        return [lesson for lesson, _ in scored]

    def retrieve_with_scores(
        self,
        query: str,
        top_k: int = 3,
        score_threshold: float = 0.0,
        max_retries: int = 3,
    ) -> List[Tuple[Lesson, float]]:
        """Same as ``retrieve`` but pairs every Lesson with its relevance
        score so downstream callers can rerank (e.g. by app match) without
        re-issuing the query."""
        results: List[Tuple[Any, float]] = []
        for attempt in range(1, max_retries + 1):
            try:
                raw = self._read(
                    lambda: self.database.similarity_search_with_relevance_scores(
                        query,
                        k=top_k,
                    )
                )
                # apply threshold: better to return 0 than return noise
                if score_threshold is not None:
                    raw = [(d, s) for d, s in raw if s >= score_threshold]
                results = raw
                break
            except Exception:
                if attempt == max_retries:
                    return []

        self.logger.debug(message=f"retrieved {len(results)} entries (with scores)")
        return [(Lesson(**doc.metadata), float(score)) for doc, score in results]

    def bind_merge_rule(self, rule: MergeRule) -> "LessonMemory":
        """Attach a merge rule to fire on every add."""
        self.merge_rules.append(rule)
        return self

    def unbind_merge_rule(self, rule: MergeRule) -> "LessonMemory":
        """Detach a previously bound merge rule."""
        if rule in self.merge_rules:
            self.merge_rules.remove(rule)
        return self
