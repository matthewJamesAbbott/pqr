from __future__ import annotations

"""Shared imports and utilities used across the pqr package."""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# History
HISTORY_FILE = Path.home() / ".pqr_history"
MAX_HISTORY = 10


def _is_container(v: Any) -> bool:
    return isinstance(v, (list, pd.Series)) or hasattr(v, "shape")


def _is_zst_file(path: str) -> bool:
    return path.endswith(".zst") or path.endswith(".zst.")


def _load_history() -> list[str]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _save_history(files: list[str]) -> None:
    HISTORY_FILE.write_text(json.dumps(files[:MAX_HISTORY]))


def _add_to_history(path: str) -> None:
    files = _load_history()
    files = [path] + [f for f in files if f != path]
    _save_history(files[:MAX_HISTORY])
