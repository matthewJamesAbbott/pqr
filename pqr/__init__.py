from __future__ import annotations

from pqr.common import (
    HISTORY_FILE, MAX_HISTORY, _add_to_history, _is_container, _is_zst_file,
    _load_history, _save_history,
    asyncio, base64, json, os, pd, pa, pq, re, subprocess, sys,
)

__all__ = [
    # Common
    "pd", "pa", "pq",
    "asyncio", "base64", "json", "os", "re", "subprocess", "sys",
    "_is_container", "_is_zst_file",
    "_add_to_history", "_load_history", "_save_history",
    "HISTORY_FILE", "MAX_HISTORY",

    # Lazy — imported on demand
    "Reader", "ParquetReader", "JsonlReader", "ReaderFactory",
    "PipelineState", "StepResult",
    "Pipeline", "Step",
    "Command",
    "PQRSTask",
    "ParquetReaderApp",
    "main",
]

# Resolve lazy names from __all__ at import time
def __getattr__(name):
    if name == "Reader" or name == "ParquetReader" or name == "JsonlReader" or name == "ReaderFactory":
        from pqr.io.reader import Reader, ParquetReader, JsonlReader, ReaderFactory
        return {"Reader": Reader, "ParquetReader": ParquetReader, "JsonlReader": JsonlReader, "ReaderFactory": ReaderFactory}[name]
    if name == "PipelineState" or name == "StepResult":
        from pqr.pipeline.state import PipelineState, StepResult
        return {"PipelineState": PipelineState, "StepResult": StepResult}[name]
    if name == "Pipeline" or name == "Step":
        from pqr.pipeline.engine import Pipeline, Step
        return {"Pipeline": Pipeline, "Step": Step}[name]
    if name == "Command":
        from pqr.commands.base import Command
        return Command
    if name == "PQRSTask":
        from pqr.app.facade import PQRSTask
        return PQRSTask
    if name == "ParquetReaderApp":
        from pqr.app.tui import ParquetReaderApp
        return ParquetReaderApp
    if name == "main":
        from pqr.app.tui import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
