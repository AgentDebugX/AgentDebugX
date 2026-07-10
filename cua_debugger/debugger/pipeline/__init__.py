"""RCA pipeline: classify -> ingest -> RCA -> tagger -> memory."""

__all__ = ["run_full_pipeline"]


def __getattr__(name):
    if name == "run_full_pipeline":
        from .orchestrator import run_full_pipeline
        globals()["run_full_pipeline"] = run_full_pipeline
        return run_full_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
