"""
Debugger memory package.

Public API:
    from debugger.memory import EpisodicMemory, TrajectoryType
    from debugger.memory import Annotation, load_annotation
    from debugger.memory import build_error_context, extract_intention
    from debugger.memory import Lesson, distill_lesson
"""

from .annotation_loader import (
    Annotation,
    load_annotation,
    load_annotations,
    load_debugger_refs,
)


def __getattr__(name):
    if name in {"EpisodicMemory", "TrajectoryType"}:
        from .episode_memory import EpisodicMemory, TrajectoryType
        globals()["EpisodicMemory"] = EpisodicMemory
        globals()["TrajectoryType"] = TrajectoryType
        return globals()[name]
    if name in {"build_error_context", "extract_intention"}:
        from .intention import build_error_context, extract_intention
        globals()["build_error_context"] = build_error_context
        globals()["extract_intention"] = extract_intention
        return globals()[name]
    if name in {"Lesson", "LessonMemory", "MapMergeRule"}:
        from .lesson_memory import Lesson, LessonMemory, MapMergeRule
        globals()["Lesson"] = Lesson
        globals()["LessonMemory"] = LessonMemory
        globals()["MapMergeRule"] = MapMergeRule
        return globals()[name]
    if name in {"distill_lesson", "distill_contrastive", "distill_from_annotation"}:
        from .distill import distill_lesson, distill_contrastive, distill_from_annotation
        globals()["distill_lesson"] = distill_lesson
        globals()["distill_contrastive"] = distill_contrastive
        globals()["distill_from_annotation"] = distill_from_annotation
        return globals()[name]
    if name in {"LessonInjector", "CompositeSelector", "CompositeWeights",
                "HtmlTaxonomySheetRenderer", "LessonSelector", "TaxonomySheetRenderer"}:
        from .lesson_injector import (
            LessonInjector, CompositeSelector, CompositeWeights,
            HtmlTaxonomySheetRenderer, LessonSelector, TaxonomySheetRenderer,
        )
        globals()["LessonInjector"] = LessonInjector
        globals()["CompositeSelector"] = CompositeSelector
        globals()["CompositeWeights"] = CompositeWeights
        globals()["HtmlTaxonomySheetRenderer"] = HtmlTaxonomySheetRenderer
        globals()["LessonSelector"] = LessonSelector
        globals()["TaxonomySheetRenderer"] = TaxonomySheetRenderer
        return globals()[name]
    if name in {"SemanticMergeRule", "MergeRouter", "RouterThresholds",
                "HyDeLessonLookup",
                "AddAction", "UpdateAction", "MergeAction", "NoopAction"}:
        from .semantic_merge import (
            SemanticMergeRule, MergeRouter, RouterThresholds, HyDeLessonLookup,
            AddAction, UpdateAction, MergeAction, NoopAction,
        )
        globals()["SemanticMergeRule"] = SemanticMergeRule
        globals()["MergeRouter"] = MergeRouter
        globals()["RouterThresholds"] = RouterThresholds
        globals()["HyDeLessonLookup"] = HyDeLessonLookup
        globals()["AddAction"] = AddAction
        globals()["UpdateAction"] = UpdateAction
        globals()["MergeAction"] = MergeAction
        globals()["NoopAction"] = NoopAction
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "EpisodicMemory",
    "Lesson",
    "LessonMemory",
    "MapMergeRule",
    "TrajectoryType",
    "Annotation",
    "load_annotation",
    "load_annotations",
    "load_debugger_refs",
    "build_error_context",
    "extract_intention",
    "distill_lesson",
    "distill_contrastive",
    "distill_from_annotation",
    # injector + selector
    "LessonInjector",
    "CompositeSelector",
    "CompositeWeights",
    "HtmlTaxonomySheetRenderer",
    "LessonSelector",
    "TaxonomySheetRenderer",
    # semantic merge
    "SemanticMergeRule",
    "MergeRouter",
    "RouterThresholds",
    "HyDeLessonLookup",
    "AddAction",
    "UpdateAction",
    "MergeAction",
    "NoopAction",
]
