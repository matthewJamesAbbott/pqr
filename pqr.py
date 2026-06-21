#!/usr/bin/env python3
"""
pqr - Parquet Reader: a vim-like terminal viewer and editor for .parquet files.
      Built on the Textual TUI framework (https://textual.textualize.io/)
      Think of it as "less" or "vim" but for columnar parquet data.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import argparse
import asyncio
import base64
import json
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# zstandard support (lazy import)
# ---------------------------------------------------------------------------
try:
    import zstandard as zstd
except ImportError:
    zstd = None

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import (
    DataTable, Footer, Input, Label, RichLog, Static,
    DirectoryTree,
)
from textual.containers import Container, ScrollableContainer
from textual.message import Message
from rich.markup import escape
from rich.text import Text

# ---------------------------------------------------------------------------
# Step / Pipeline — operation abstraction usable from CLI and TUI
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    df: Optional[pd.DataFrame] = None
    message: str = ""
    yanked: str = ""
    output: Optional[Any] = None


@dataclass
class Step:
    name: str
    args: dict = field(default_factory=dict)

    @staticmethod
    def parse_spec(spec: str) -> "Step":
        """Parse 'name', 'name:key=value;key2=value2', or 'sql:SELECT * FROM df'."""
        if ":" in spec:
            name, rest = spec.split(":", 1)
            name = name.strip()
            rest = rest.strip()
            # Steps that take a plain expression (no key= prefix): sql, filter, python, shell, search
            expr_steps = {"sql", "filter", "python", "shell", "search"}
            if name in expr_steps:
                return Step(name=name, args={"expr": rest})
            # Parse key=value pairs separated by ;
            args = {}
            for part in rest.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    args[k.strip()] = v.strip()
                elif part:
                    args["expr"] = part
            return Step(name=name, args=args)
        return Step(name=spec.strip())


@dataclass
class PipelineState:
    df: Optional[pd.DataFrame] = None
    schema: Optional[pa.Schema] = None
    path: Optional[Path] = None
    hidden_cols: set = field(default_factory=set)
    sort_col: Optional[str] = None
    sort_asc: bool = True
    clipboard: str = ""
    messages: list[str] = field(default_factory=list)
    args: dict = field(default_factory=dict)
    _zst_reader: Optional["LazyJsonlReader"] = None


def _is_container(v: Any) -> bool:
    return isinstance(v, (list, pd.Series)) or hasattr(v, "shape")


def _try_copy(text: str) -> bool:
    """Copy text to system clipboard, trying all available backends."""
    import base64
    import os
    import subprocess

    def _try_cmd(cmd: list[str]) -> bool:
        try:
            subprocess.run(cmd, input=text.encode(), capture_output=True, check=True)
            return True
        except Exception:
            return False

    def _osc52(device: str) -> bool:
        try:
            b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
            osc = f"\x1b]52;c;{b64}\x07"
            with open(device, "w") as tty:
                tty.write(osc)
                tty.flush()
            return True
        except Exception:
            return False

    if os.environ.get("DISPLAY") and _try_cmd(["xclip", "-selection", "clipboard"]):
        return True
    if os.environ.get("WAYLAND_DISPLAY") and _try_cmd(["wl-copy"]):
        return True
    if _try_cmd(["pbcopy"]):
        return True
    if _try_cmd(["clip"]):
        return True

    tty_candidates = []
    if os.environ.get("TMUX"):
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{client_tty}"],
                capture_output=True, text=True
            )
            client_tty = result.stdout.strip()
            if client_tty.startswith("/dev/"):
                tty_candidates.append(client_tty)
        except Exception:
            pass
    if os.environ.get("SSH_TTY"):
        tty_candidates.append(os.environ["SSH_TTY"])
    tty_candidates.append("/dev/tty")
    try:
        own_fd = os.readlink("/proc/self/fd/0")
        if own_fd.startswith("/dev/"):
            tty_candidates.append(own_fd)
    except Exception:
        pass
    seen = set()
    for dev in tty_candidates:
        if dev not in seen:
            seen.add(dev)
            if _osc52(dev):
                return True
    return False


# -- Built-in step implementations ------------------------------------------

def _step_schema(state: PipelineState) -> StepResult:
    buf = f"File: {state.path}\n"
    if _is_zst_file(str(state.path)):
        reader = state.args.get("_zst_reader")
        if isinstance(reader, LazyJsonlReader):
            buf += f"Columns: {len(reader.columns)}\n"
            buf += f"Rows: {reader.num_rows}\n\n"
            buf += "Column Details:\n"
            for i, col in enumerate(reader.columns):
                buf += f"  {i+1}. {col} : {reader.dtypes.get(col, 'object')}\n"
            return StepResult(df=state.df, message=buf)
        # Batch mode: the reader is not passed, build a new one
        if hasattr(state, '_zst_reader') and state._zst_reader is not None:
            buf += f"Columns: {len(state._zst_reader.columns)}\n"
            buf += f"Rows: {state._zst_reader.num_rows}\n\n"
            buf += "Column Details:\n"
            for i, col in enumerate(state._zst_reader.columns):
                buf += f"  {i+1}. {col} : {state._zst_reader.dtypes.get(col, 'object')}\n"
        else:
            buf += "Columns: ?\nRows: ?\n"
        return StepResult(df=state.df, message=buf)
    meta = pq.read_metadata(str(state.path))
    buf += f"Columns: {len(state.schema)}\n"
    buf += f"Row groups: {meta.num_row_groups}\n"
    buf += f"Rows: {meta.num_rows}\n\n"
    buf += "Column Details:\n"
    for i in range(len(state.schema)):
        fld = state.schema[i]
        col_meta = meta.row_group(0).column(i)
        buf += f"  {i+1}. {fld.name} : {fld.type}\n"
        stats = col_meta.statistics
        buf += f"    null_count: {stats.null_count if stats else 'N/A'}\n"
        buf += f"    compressed_size: {col_meta.total_compressed_size} bytes\n"
    return StepResult(df=state.df, message=buf)


def _step_yank(state: PipelineState) -> StepResult:
    col = state.args.get("column") or state.args.get("expr")
    row = state.args.get("row")
    if col is None:
        return StepResult(df=state.df, message="Yank requires --column or --row")
    df = state.df
    if df is None or col not in df.columns:
        return StepResult(df=state.df, message=f"Column '{col}' not found")
    if row is not None:
        row = int(row)
        if row < 0:
            row = len(df) + row
        val = df[col].iloc[row]
    else:
        val = df[col].to_list()
    text = str(val)
    _try_copy(text)
    state.clipboard = text
    return StepResult(df=state.df, message=f"Yanked: {text[:120]}", yanked=text)


def _step_search(state: PipelineState) -> StepResult:
    pattern = state.args.get("expr", "").lower()
    if not pattern or state.df is None:
        return StepResult(df=state.df, message="No data to search")
    df = state.df
    matches = []
    cols = [c for c in df.columns if c not in state.hidden_cols]
    for ri in range(len(df)):
        for ci, col in enumerate(cols):
            val = df.iloc[ri, ci]
            if _is_container(val):
                if val.size > 0 and pattern in str(val).lower():
                    matches.append((ri, col))
            elif val is not None and not pd.isna(val):
                if pattern in str(val).lower():
                    matches.append((ri, col))
    detail = f"\n".join(f"  row {r}, col {c}" for r, c in matches[:50])
    if len(matches) > 50:
        detail += f"\n  ... and {len(matches)-50} more"
    return StepResult(df=state.df, message=f"Found {len(matches)} matches:\n{detail}")


def _step_filter(state: PipelineState) -> StepResult:
    expr = state.args.get("expr", "")
    if not expr or state.df is None:
        return StepResult(df=state.df, message="No filter expression")
    df = state.df
    filtered = df.query(expr)
    return StepResult(df=filtered, message=f"Filtered: {len(filtered)} / {len(df)} rows")


def _step_sort(state: PipelineState) -> StepResult:
    col = state.args.get("column") or state.args.get("expr")
    desc = state.args.get("desc", "false").lower() == "true"
    if not col or state.df is None:
        return StepResult(df=state.df, message="Sort requires --column")
    df = state.df.copy()
    if df.empty:
        return StepResult(df=state.df, message="Empty DataFrame")
    if _is_container(df[col].iloc[0]):
        df["_sort_key"] = df[col].apply(
            lambda x: str(x) if (hasattr(x, "size") and x.size > 0) else ""
        )
        df = df.sort_values("_sort_key", ascending=not desc)
        df.drop(columns=["_sort_key"], inplace=True)
    else:
        df = df.sort_values(col, ascending=not desc)
    df = df.reset_index(drop=True)
    return StepResult(
        df=df,
        message=f"Sorted by {col} {'desc' if desc else 'asc'}"
    )


def _step_hide(state: PipelineState) -> StepResult:
    col = state.args.get("column") or state.args.get("expr")
    if not col or state.df is None:
        return StepResult(df=state.df, message="Hide requires --column")
    state.hidden_cols.add(col)
    return StepResult(df=state.df, message=f"Hidden column: {col}")


def _step_sql(state: PipelineState) -> StepResult:
    expr = state.args.get("expr", "")
    if not expr or state.df is None:
        return StepResult(df=state.df, message="No SQL expression")
    try:
        import duckdb
        con = duckdb.connect()
        con.register("df", state.df)
        result = con.execute(expr).fetchdf()
        return StepResult(df=result, message=f"SQL result: {len(result)} rows")
    except ImportError:
        return StepResult(df=state.df, message="Install duckdb: pip install duckdb")
    except Exception as e:
        return StepResult(df=state.df, message=f"SQL error: {e}")


def _step_stats(state: PipelineState) -> StepResult:
    col = state.args.get("column")
    if state.df is None:
        return StepResult(df=state.df, message="No data")
    df = state.df
    cols = [col] if col else [c for c in df.columns if c not in state.hidden_cols]
    buf = "Stats:\n"
    for c in cols:
        series = df[c].dropna()
        if series.empty:
            buf += f"  {c}: all null\n"
            continue
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if len(numeric) > 0:
            buf += (f"  {c}: count={len(numeric)}, "
                    f"mean={numeric.mean():.2f}, "
                    f"min={numeric.min():.2f}, max={numeric.max():.2f}\n")
        else:
            buf += f"  {c}: count={len(series)} (non-numeric)\n"
    return StepResult(df=state.df, message=buf)


def _step_delete_row(state: PipelineState) -> StepResult:
    row = state.args.get("row", "0")
    row = int(row)
    if state.df is None or len(state.df) == 0:
        return StepResult(df=state.df, message="No data")
    if row < 0:
        row = len(state.df) + row
    if row >= len(state.df) or row < 0:
        return StepResult(df=state.df, message=f"Row index {row} out of range")
    df = state.df.drop(index=row).reset_index(drop=True)
    return StepResult(df=df, message=f"Deleted row {row}")


def _step_export(state: PipelineState) -> StepResult:
    fmt = state.args.get("format", "csv")
    out = state.args.get("output", "-")
    if state.df is None:
        return StepResult(df=state.df, message="No data to export")
    if fmt == "json":
        buf = state.df.to_json(orient="records", indent=2)
    elif fmt == "parquet":
        if out == "-":
            return StepResult(df=state.df, message="Parquet export requires a file path")
        state.df.to_parquet(out, index=False)
        return StepResult(df=state.df, message=f"Exported to {out}")
    else:
        buf = state.df.to_csv(index=False)
    return StepResult(df=state.df, message=buf, output=buf)


def _step_python(state: PipelineState) -> StepResult:
    expr = state.args.get("expr", "")
    if not expr or state.df is None:
        return StepResult(df=state.df, message="No Python expression")
    local_ns = {"df": state.df, "pd": pd}
    try:
        result = eval(expr, {"__builtins__": __builtins__}, local_ns)
        if isinstance(result, pd.DataFrame):
            return StepResult(df=result, message=f"Python result: {len(result)} rows")
        return StepResult(df=state.df, message=f"Python: {result}")
    except Exception as e:
        return StepResult(df=state.df, message=f"Python error: {e}")


def _step_shell(state: PipelineState) -> StepResult:
    cmd = state.args.get("expr", "")
    if not cmd or state.df is None:
        return StepResult(df=state.df, message="No shell command")
    try:
        csv_data = state.df.to_csv(index=False)
        proc = subprocess.run(
            cmd, shell=True, input=csv_data,
            capture_output=True, check=True, text=True
        )
        return StepResult(df=state.df, message=proc.stdout)
    except subprocess.CalledProcessError as e:
        return StepResult(df=state.df, message=f"Shell error: {e.stderr}")
    except Exception as e:
        return StepResult(df=state.df, message=f"Shell error: {e}")


_STEP_MAP = {
    "schema": _step_schema,
    "yank": _step_yank,
    "search": _step_search,
    "filter": _step_filter,
    "sort": _step_sort,
    "hide": _step_hide,
    "sql": _step_sql,
    "stats": _step_stats,
    "delete-row": _step_delete_row,
    "delete_row": _step_delete_row,
    "delrow": _step_delete_row,
    "export": _step_export,
    "python": _step_python,
    "shell": _step_shell,
}


class Pipeline:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(self, step: Step) -> None:
        self.steps.append(step)

    def add_spec(self, spec: str) -> None:
        self.add(Step.parse_spec(spec))

    def run(self, state: PipelineState) -> list[StepResult]:
        results = []
        for step in self.steps:
            handler = _STEP_MAP.get(step.name)
            if handler is None:
                results.append(StepResult(
                    df=state.df,
                    message=f"[red]Unknown step: {step.name}[/red]"
                ))
                continue
            state.args = dict(step.args)
            result = handler(state)
            if result.df is not None:
                state.df = result.df
            if result.message:
                state.messages.append(result.message)
            if result.yanked:
                state.clipboard = result.yanked
            results.append(result)
        return results


def _is_zst_file(path: str) -> bool:
    """Check if path is a .zst (zstandard-compressed JSONL) file."""
    return path.endswith(".zst") or path.endswith(".zst.")


def _build_state(path: str, schema: Optional[pa.Schema] = None) -> PipelineState:
    if _is_zst_file(path):
        return _build_state_zst(path)
    df = pq.read_table(path).to_pandas()
    pq_file = pq.ParquetFile(path)
    return PipelineState(
        df=df,
        schema=schema or pq_file.schema_arrow,
        path=Path(path),
    )


# ---------------------------------------------------------------------------
# LazyJsonlReader — lazy, chunked reader for .zst JSONL files
# ---------------------------------------------------------------------------

class LazyJsonlReader:
    """Streaming reader for compressed JSONL, mimicking ParquetFile lazy loading.

    Phase 1 (instant): sample first 100 MB compressed → ~5 K rows + schema.
    Phase 2 (streaming): a single ``stream_reader`` walks the decompressed
    output and fills a row cache.  Forward scrolls reuse the stream;
    backward jumps reopen from the beginning.
    """

    SAMPLE_COMPRESSED_BYTES = 100 * 1024 * 1024
    CHUNK_ROWS = 3000

    def __init__(self, path: str) -> None:
        self._path = path
        self._file_size = os.path.getsize(path)
        self._decompressor = zstd.ZstdDecompressor()
        self._stream_reader: Any = None
        self._stream_open: bool = False
        self._num_rows: int = 0
        self._all_columns: list[str] = []
        self._dtypes: dict[str, str] = {}
        self._rows_per_mb: float = 0.0
        self._cache: list[dict] = []
        self._cache_start: int = 0
        self._cache_end: int = 0
        self._eof: bool = False

    @property
    def num_rows(self) -> int:
        return self._num_rows

    @property
    def columns(self) -> list[str]:
        return self._all_columns

    @property
    def dtypes(self) -> dict[str, str]:
        return self._dtypes

    def _ensure_indexed(self) -> None:
        if self._num_rows > 0:
            return
        self._sample_index()

    def _sample_index(self) -> None:
        with open(self._path, "rb") as f:
            sr = self._decompressor.stream_reader(f)
            raw = sr.read(self.SAMPLE_COMPRESSED_BYTES)
            sr.close()
        text = raw.decode("utf-8", errors="replace")
        non_empty = [l for l in text.split("\n") if l.strip()]
        sample_records = []
        for line in non_empty[:5]:
            try:
                sample_records.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        self._rows_per_mb = len(non_empty) / (self.SAMPLE_COMPRESSED_BYTES / 1024 / 1024)
        # For small files where entire content fits in sample, use actual count
        sample_file_mb = self.SAMPLE_COMPRESSED_BYTES / 1024 / 1024
        file_mb = self._file_size / 1024 / 1024
        if file_mb <= sample_file_mb:
            self._num_rows = len(non_empty)
        else:
            self._num_rows = int(self._rows_per_mb * file_mb)
        self._all_columns = []
        self._dtypes = {}
        for rec in sample_records:
            self._flatten_record(rec, self._all_columns, self._dtypes)
        self._cache = []
        for line in non_empty[:self.CHUNK_ROWS]:
            try:
                self._cache.append(self._flatten_to_dict(json.loads(line)))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        self._cache_start = 0
        self._cache_end = len(self._cache)

    def _open_stream(self) -> None:
        if self._stream_reader is not None:
            try:
                self._stream_reader.close()
            except Exception:
                pass
        self._file = open(self._path, "rb")
        self._stream_reader = self._decompressor.stream_reader(self._file)
        self._stream_open = True

    def _fill_cache_from(self, target_row: int) -> None:
        if target_row < self._cache_end and target_row >= self._cache_start:
            return
        need_reopen = target_row < self._cache_start or not self._cache or not self._stream_open
        if need_reopen:
            self._open_stream()
            self._cache = []
            self._cache_start = 0
            self._cache_end = 0
            self._eof = False
        produced = 0
        buffer = b""
        target = target_row - self._cache_start + self.CHUNK_ROWS
        while produced < target and not self._eof:
            chunk = self._stream_reader.read(1048576)
            if not chunk:
                self._eof = True
                if buffer.strip():
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(buffer.strip().decode("utf-8"))))
                        produced += 1
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    produced += 1
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(line.decode("utf-8"))))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
        self._cache_end = self._cache_start + len(self._cache)

    def get_row_range(self, start: int, end: int) -> pd.DataFrame:
        self._ensure_indexed()
        if start >= self._num_rows:
            return pd.DataFrame(columns=self._all_columns)
        end = min(end, self._num_rows)
        n_rows = end - start
        self._fill_cache_from(start)
        idx_start = start - self._cache_start
        idx_end = end - self._cache_start
        records = self._cache[idx_start:idx_end]
        return pd.DataFrame(records[:n_rows], columns=self._all_columns)

    @staticmethod
    def _flatten_record(rec: dict, columns: list[str], dtypes: dict[str, str]) -> None:
        def _walk(obj, prefix: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, list):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "list"
            elif isinstance(obj, bool):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "bool"
            elif isinstance(obj, int):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "int64"
            elif isinstance(obj, float):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "float64"
            elif obj is None:
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "object"
            elif isinstance(obj, str):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "object"
        _walk(rec, "")

    @staticmethod
    def _flatten_to_dict(rec: dict) -> dict:
        result = {}
        def _walk(obj, prefix: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, list):
                result[prefix] = json.dumps(obj)
            else:
                result[prefix] = obj
        _walk(rec, "")
        return result

    def close(self) -> None:
        try:
            if self._stream_reader:
                self._stream_reader.close()
        except Exception:
            pass
        try:
            if hasattr(self, "_file"):
                self._file.close()
        except Exception:
            pass


def _build_state_zst(path: str) -> PipelineState:
    """Build PipelineState for a .zst JSONL file (lazy)."""
    reader = LazyJsonlReader(path)
    reader._ensure_indexed()
    initial = reader.get_row_range(0, min(reader.CHUNK_ROWS, reader.num_rows))
    state = PipelineState(
        df=initial,
        schema=None,
        path=Path(path),
        args={"_zst_reader": reader, "_zst": True},
    )
    state._zst_reader = reader
    return state


# ---------------------------------------------------------------------------
# Batch zst reader for CLI / pipeline steps
# ---------------------------------------------------------------------------

def _build_zst_reader(path: str) -> LazyJsonlReader:
    """Create a LazyJsonlReader, ensuring the index is built."""
    reader = LazyJsonlReader(path)
    reader._ensure_indexed()
    return reader

# ---------------------------------------------------------------------------
# Recent-file history helper
# ---------------------------------------------------------------------------
HISTORY_FILE = Path.home() / ".pqr_history"
MAX_HISTORY = 10


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


# ---------------------------------------------------------------------------
# ViewCellScreen — full cell text popup
# ---------------------------------------------------------------------------
class ViewCellScreen(Screen[None]):
    CSS = """
        Screen {
            align: center middle;
            background: black 70%;
        }
        #view-container {
            width: 80%;
            height: 80%;
            background: $surface;
            border: solid $accent;
            padding: 1;
            overflow-y: auto;
        }
        #view-text {
            height: auto;
            width: 100%;
        }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("j,down", "scroll_down", "Down"),
        Binding("k,up", "scroll_up", "Up"),
        Binding("pgdown", "scroll_down", "PgDn"),
        Binding("pgup", "scroll_up", "PgUp"),
    ]

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def action_close(self) -> None:
        self.dismiss()

    def action_scroll_down(self) -> None:
        self.query_one("#view-container", Container).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#view-container", Container).scroll_up()

    def on_mount(self) -> None:
        self.query_one("#view-text", Static).focus()

    def compose(self) -> ComposeResult:
        yield Container(
            Static(self._text, id="view-text", markup=False),
            id="view-container",
        )


# ---------------------------------------------------------------------------
# EditScreen — single cell editor popup
# ---------------------------------------------------------------------------
class EditScreen(Screen[bool]):
    CSS = """
        Screen {
            align: center middle;
            background: black 60%;
        }
        #ed-container {
            width: 60%;
            height: auto;
        }
        #ed-label {
            color: $accent;
        }
    """

    BINDINGS = [
        Binding("enter,ctrl+j", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        parent: "ParquetReader",
        row_idx: int,
        col_idx: int,
        value: str,
        original: str,
        row_key: str,
        col_key: str,
        append: bool = False,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._row_idx = row_idx
        self._col_idx = col_idx
        self._value = value
        self._original = original
        self._row_key = row_key
        self._col_key = col_key
        self._append = append

    def compose(self) -> ComposeResult:
        yield Label(f" [{self._col_key}]  row {self._row_idx}", id="ed-label")
        yield Input(id="ed-input", value=self._value)

    def on_mount(self) -> None:
        inp = self.query_one("#ed-input", Input)
        inp.focus()
        if self._append:
            inp.cursor_position = len(inp.value)

    def action_confirm(self) -> None:
        new_val = self.query_one("#ed-input", Input).value
        self._parent.edit_cell(
            self._row_idx, self._col_idx,
            self._row_key, self._col_key,
            new_val, self._original,
        )
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# SchemaScreen — parquet schema details
# ---------------------------------------------------------------------------
class SchemaScreen(Screen[None]):
    CSS = """
        Screen {
            background: $boost;
        }
        #schema-log {
            width: 90%;
            height: 80%;
            background: $surface;
            border: solid $accent;
            padding: 1 2;
            overflow-y: auto;
        }
        #schema-title {
            dock: top;
            height: 1;
            content-align: center middle;
            background: $accent;
            color: $surface;
        }
    """

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
    ]

    def __init__(self, path: str, schema: pa.Schema) -> None:
        super().__init__()
        self._path = path
        self._schema = schema

    def action_close(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        yield Label(f" Schema: {Path(self._path).name}", id="schema-title")
        yield ScrollableContainer(
            RichLog(id="schema-log"),
        )

    def on_mount(self) -> None:
        log = self.query_one("#schema-log", RichLog)
        meta = pq.read_metadata(self._path)
        log.write(f"[bold cyan]File:[/bold cyan] {self._path}\n")
        log.write(f"[bold cyan]Columns:[/bold cyan] {len(self._schema)}\n")
        log.write(f"[bold cyan]Row groups:[/bold cyan] {meta.num_row_groups}\n")
        log.write(f"[bold cyan]Rows:[/bold cyan] {meta.num_rows}\n\n")
        log.write("[bold]Column Details:[/bold]\n")
        for i in range(len(self._schema)):
            field = self._schema[i]
            col_meta = meta.row_group(0).column(i)
            log.write(f"  [cyan]{i+1}.[/cyan] [bold]{field.name}[/bold] : {field.type}")
            encodings = getattr(col_meta, "encodings", None)
            if encodings:
                log.write(f"    encodings: {encodings}")
            stats = col_meta.statistics
            log.write(f"    null_count: {stats.null_count if stats else 'N/A'}")
            log.write(f"    compressed_size: {col_meta.total_compressed_size} bytes\n")


# ---------------------------------------------------------------------------
# FileBrowserScreen — directory tree file picker
# ---------------------------------------------------------------------------
class FileBrowserScreen(Screen[Optional[str]]):
    """File picker using DirectoryTree (compatible with Textual 8.x)."""

    CSS = """
        Screen {
            align: center middle;
            background: black 60%;
        }
        #fb-container {
            width: 55%;
            height: 85%;
            background: $surface;
            border: solid $accent;
            padding: 1;
        }
        #file-tree {
            height: 100%;
            overflow-y: auto;
        }
        #fb-title {
            dock: top;
            height: 1;
            content-align: center middle;
            background: $accent;
            color: $surface;
        }
        #fb-hint {
            dock: bottom;
            height: 1;
            content-align: center middle;
        }
    """

    BINDINGS = [
        Binding("enter", "open_file", "Open"),
        Binding("escape", "cancel", "Cancel"),
        Binding("backspace", "up_dir", "Up"),
    ]

    def __init__(self, path: str = ".") -> None:
        super().__init__()
        self._initial_path = Path(path).expanduser().resolve()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_up_dir(self) -> None:
        tree = self.query_one(DirectoryTree)
        current = tree.path
        parent = Path(current).parent
        if str(parent) != str(current):
            tree.path = parent

    def action_open_file(self) -> None:
        tree = self.query_one(DirectoryTree)
        node = tree.cursor_node
        if node is not None:
            path = node.path
            if path.is_file():
                self.dismiss(str(path))

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))

    def compose(self) -> ComposeResult:
        yield Label(" Browse Files — Enter to select  Backspace to go up  Escape to cancel", id="fb-title")
        yield Container(
            DirectoryTree(self._initial_path, id="file-tree"),
            id="fb-container",
        )
        yield Label("Press Enter on a file to select  |  Backspace for parent  |  Escape to cancel", id="fb-hint")


# ---------------------------------------------------------------------------
# RecentFilesScreen — shows recent files list
# ---------------------------------------------------------------------------
class RecentFilesScreen(Screen[Optional[str]]):
    CSS = """
        Screen {
            background: $boost;
        }
        #recent-title {
            dock: top;
            height: 2;
            content-align: center middle;
            background: $accent;
            color: $surface;
        }
        #recent-list {
            width: 50%;
            height: auto;
            margin: 2 20%;
            background: $surface;
            border: solid $accent;
            padding: 1;
        }
        #recent-hint {
            dock: bottom;
            height: 1;
            content-align: center middle;
        }
        .recent-file {
            white-space: wrap;
            padding: 0 1;
        }
    """

    BINDINGS = [
        Binding("j,down", "down", "Down"),
        Binding("k,up", "up", "Up"),
        Binding("enter", "select", "Open"),
        Binding("o,ctrl+o", "open_file", "Browse"),
        Binding("escape,q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._files: list[str] = []
        self._cursor: int = 0

    def on_mount(self) -> None:
        self._files = _load_history()
        self._refresh()

    def _refresh(self) -> None:
        container = self.query_one("#recent-list", Container)
        for w in list(container.children):
            w.remove()
        if not self._files:
            container.mount(Static("No recent files.", classes="recent-file"))
        else:
            for i, f in enumerate(self._files):
                exists = "[green]OK[/green]" if Path(f).exists() else "[red]MISSING[/red]"
                container.mount(
                    Static(
                        f"{'>> ' if i == self._cursor else '   '}{exists}  {f}",
                        classes="recent-file",
                    )
                )

    def action_down(self) -> None:
        if self._files:
            self._cursor = min(self._cursor + 1, len(self._files) - 1)
            self._refresh()

    def action_up(self) -> None:
        if self._files:
            self._cursor = max(self._cursor - 1, 0)
            self._refresh()

    def action_select(self) -> None:
        if self._files and 0 <= self._cursor < len(self._files):
            self.dismiss(self._files[self._cursor])

    def action_open_file(self) -> None:
        self.push_screen(
            FileBrowserScreen(),
            callback=lambda path: self._on_file_selected(path),
        )

    def _on_file_selected(self, path: Optional[str]) -> None:
        if path:
            self.dismiss(path)

    def action_quit(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        yield Label(" pqr — Recent Files", id="recent-title")
        yield Container(id="recent-list")
        yield Label("j/k navigate  Enter open  o browse  q quit", id="recent-hint")


# ---------------------------------------------------------------------------
# DirBrowserScreen — lists .parquet files in a directory
# ---------------------------------------------------------------------------
class DirBrowserScreen(Screen[Optional[str]]):
    CSS = """
        Screen {
            background: $boost;
        }
        #dir-title {
            dock: top;
            height: 1;
            content-align: center middle;
            background: $accent;
            color: $surface;
        }
        #dir-table {
            width: 80%;
            height: auto;
            margin: 1 10%;
            background: $surface;
            border: solid $accent;
        }
        #dir-hint {
            dock: bottom;
            height: 1;
            content-align: center middle;
        }
    """

    BINDINGS = [
        Binding("j,down", "down", "Down"),
        Binding("k,up", "up", "Up"),
        Binding("enter", "select", "Open"),
        Binding("escape,q", "close", "Close"),
    ]

    def __init__(self, directory: str) -> None:
        super().__init__()
        self._directory = directory
        self._files: list[str] = []
        self._dt: DataTable | None = None

    def on_mount(self) -> None:
        dt = DataTable(id="dir-table", show_cursor=True, zebra_stripes=True)
        dt.add_columns("Filename", "Rows", "Columns", "Size")
        self.mount(dt)
        self._dt = dt

        parquet_files = sorted(
            str(p) for p in Path(self._directory).glob("**/*.parquet")
        )
        zst_files = sorted(
            str(p) for p in Path(self._directory).glob("**/*.zst")
        )
        self._files = parquet_files + zst_files

        rows = []
        for fpath in self._files:
            try:
                fsize = Path(fpath).stat().st_size
                size_str = f"{fsize/1024:.1f}K" if fsize < 1024*1024 else f"{fsize/1024/1024:.1f}M"
                if fpath.endswith(".parquet"):
                    meta = pq.read_metadata(fpath)
                    n_rows = meta.num_rows
                    n_cols = meta.num_columns
                else:
                    if zstd is not None:
                        reader = LazyJsonlReader(fpath)
                        reader._ensure_indexed()
                        n_rows = reader.num_rows
                        n_cols = len(reader.columns)
                        reader.close()
                    else:
                        n_rows = "?"
                        n_cols = "?"
            except Exception:
                n_rows, n_cols, size_str = "?", "?", "?"
            rows.append([Path(fpath).name, str(n_rows), str(n_cols), size_str])

        if rows:
            dt.add_rows(rows)
        else:
            dt.add_row("No .parquet or .zst files found", "0", "0", "0B")

    def action_down(self) -> None:
        if self._dt:
            self._dt.move_cursor(row=(self._dt.cursor_row or 0) + 1)

    def action_up(self) -> None:
        if self._dt:
            self._dt.move_cursor(row=(self._dt.cursor_row or 0) - 1)

    def action_select(self) -> None:
        if self._dt is None:
            return
        row = self._dt.cursor_row
        if row is not None and row < len(self._files):
            self.dismiss(self._files[row])

    def action_close(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        yield Label(f" Directory: {self._directory}", id="dir-title")
        yield Label("j/k navigate  Enter open  q close", id="dir-hint")


# ---------------------------------------------------------------------------
# SearchScreen — global search input
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# SearchPrompt — vim-style bottom prompt (mounted inline, not a full screen)
# ---------------------------------------------------------------------------
class SearchPrompt(Container):
    """A slim input bar docked to the bottom for vim-style /search."""

    class SearchSubmitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    CSS = """
        height: 1;
        width: 100%;
        background: $accent;
        #sp-prefix {
            width: 3;
            color: $surface;
            background: $accent;
        }
        #sp-input {
            width: 1fr;
        }
    """

    def __init__(self) -> None:
        super().__init__(
            Static("/", id="sp-prefix"),
            Input(id="sp-input", placeholder="Search..."),
            id="search-prompt",
        )

    def on_mount(self) -> None:
        self.query_one("#sp-input", Input).focus()

    def on_key(self, event) -> None:
        inp = self.query_one("#sp-input", Input)
        if event.key == "enter":
            event.prevent_default()
            self.post_message(SearchPrompt.SearchSubmitted(inp.value))
            self.remove()
        elif event.key == "escape":
            event.prevent_default()
            self.remove()


# ---------------------------------------------------------------------------
# ExportScreen — choose export format
# ---------------------------------------------------------------------------
class ExportScreen(Screen[Optional[str]]):
    CSS = """
        Screen {
            align: center middle;
            background: black 70%;
        }
        #export-container {
            width: 40%;
            height: auto;
            background: $surface;
            border: solid $accent;
            padding: 1 2;
        }
        #export-title {
            color: $accent;
        }
    """

    BINDINGS = [
        Binding("j,down", "down", "Down"),
        Binding("k,up", "up", "Up"),
        Binding("c", "csv", "CSV"),
        Binding("e", "excel", "Excel"),
        Binding("p", "parquet", "Parquet"),
        Binding("escape,q", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cursor: int = 0
        self._options = ["csv", "excel", "parquet"]

    def action_down(self) -> None:
        self._cursor = min(self._cursor + 1, len(self._options) - 1)
        self._refresh()

    def action_up(self) -> None:
        self._cursor = max(self._cursor - 1, 0)
        self._refresh()

    def _refresh(self) -> None:
        for i, opt in enumerate(self._options):
            prefix = ">> " if i == self._cursor else "   "
            self.query_one(f"#export-opt-{i}", Static).update(
                f"{prefix}[{opt[0].upper()}] {opt.capitalize()}"
            )

    def action_csv(self) -> None:
        self.dismiss("csv")

    def action_excel(self) -> None:
        self.dismiss("excel")

    def action_parquet(self) -> None:
        self.dismiss("parquet")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_mount(self) -> None:
        self._refresh()

    def compose(self) -> ComposeResult:
        yield Container(
            Label("Export As (press key):", id="export-title"),
            *[Static(f"   [{o[0].upper()}] {o.capitalize()}", id=f"export-opt-{i}")
              for i, o in enumerate(self._options)],
            id="export-container",
        )


# ---------------------------------------------------------------------------
# FilterBar — docked input for column filtering
# ---------------------------------------------------------------------------
class FilterBar(Input):
    class FilterChanged(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def _on_input_changed(self) -> None:
        self.post_message(FilterBar.FilterChanged(self.value))


# ---------------------------------------------------------------------------
# PlainDataTable — plain text cell rendering
# ---------------------------------------------------------------------------
class PlainDataTable(DataTable):
    def render_cell(self, value: object) -> Text:
        return Text(str(value), no_wrap=True)


# ---------------------------------------------------------------------------
# DiffScreen — side-by-side diff of two parquet files
# ---------------------------------------------------------------------------
class DiffScreen(Screen[None]):
    CSS = """
        Screen {
            background: $boost;
        }
        #diff-title {
            dock: top;
            height: 1;
            content-align: center middle;
            background: $accent;
            color: $surface;
        }
        #diff-container {
            layout: horizontal;
            width: 100%;
            height: 1fr;
        }
        .diff-panel {
            width: 50%;
            height: 100%;
        }
        #diff-label-left, #diff-label-right {
            dock: top;
            height: 1;
            content-align: center middle;
            background: $surface;
            color: $accent;
            border: solid $border;
        }
        #diff-hint {
            dock: bottom;
            height: 1;
            content-align: center middle;
        }
    """

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
    ]

    def __init__(self, path1: str, path2: str) -> None:
        super().__init__()
        self._path1 = path1
        self._path2 = path2

    def action_close(self) -> None:
        self.dismiss()

    def on_mount(self) -> None:
        df1 = pq.read_table(self._path1).to_pandas()
        df2 = pq.read_table(self._path2).to_pandas()

        cols1 = set(df1.columns)
        cols2 = set(df2.columns)
        all_cols = sorted(cols1 | cols2)

        dt1 = DataTable(show_cursor=True, zebra_stripes=True)
        dt1.add_columns(*all_cols)
        for idx in range(len(df1)):
            row = [str(df1.iloc[idx, ci]) if all_cols[ci] in df1.columns else "(missing)" for ci in range(len(all_cols))]
            dt1.add_row(*row)
        dt1.id = "dt-left"

        dt2 = DataTable(show_cursor=True, zebra_stripes=True)
        dt2.add_columns(*all_cols)
        for idx in range(len(df2)):
            row = [str(df2.iloc[idx, ci]) if all_cols[ci] in df2.columns else "(missing)" for ci in range(len(all_cols))]
            dt2.add_row(*row)
        dt2.id = "dt-right"

        panel_left = Container(Label(f" {Path(self._path1).name}", id="diff-label-left"), dt1, classes="diff-panel")
        panel_right = Container(Label(f" {Path(self._path2).name}", id="diff-label-right"), dt2, classes="diff-panel")

        self.mount(panel_left)
        self.mount(panel_right)

    def compose(self) -> ComposeResult:
        yield Label(f" Diff: {Path(self._path1).name} vs {Path(self._path2).name}", id="diff-title")
        yield Container(id="diff-container")
        yield Label("q/Escape to close", id="diff-hint")


# ---------------------------------------------------------------------------
# ParquetReader — the main Textual application
# ---------------------------------------------------------------------------
class ParquetReader(App[None]):
    BINDINGS = [
        Binding("j,down", "down", "Down"),
        Binding("k,up", "up", "Up"),
        Binding("h,left", "left", "Left"),
        Binding("l,right", "right", "Right"),
        Binding("g", "home", "Top"),
        Binding("G", "end", "Bottom"),
        Binding("ctrl+f,pgdown", "page_down", "PgDn"),
        Binding("ctrl+b,pgup", "page_up", "PgUp"),
        Binding("i", "edit", "Edit"),
        Binding("e", "edit", "Edit"),
        Binding("a", "edit_append", "Append"),
        Binding("v", "view_cell", "View"),
        Binding("y", "yank_cell", "Yank"),
        Binding("w", "save", "Save"),
        Binding("W", "export", "Export"),
        Binding("o", "open_file", "Open"),
        Binding("O", "add_row", "AddRow"),
        Binding("dd", "delete_row", "DelRow"),
        Binding("ctrl+o", "open_file", "Open"),
        Binding("s", "sort_column", "Sort"),
        Binding("S", "schema", "Schema"),
        Binding("/", "search", "Search"),
        Binding("n", "search_next", "Next"),
        Binding("N", "search_prev", "Prev"),
        Binding("f", "filter", "Filter"),
        Binding("H", "hide_column", "HideCol"),
        Binding("x", "stats", "Stats"),
        Binding(":", "sql_query", "SQL"),
        Binding("tab", "next_tab", "NextTab"),
        Binding("shift+tab", "prev_tab", "PrevTab"),
        Binding("gt", "next_tab", "NextTab"),
        Binding("gT", "prev_tab", "PrevTab"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
        Screen {
            layout: grid;
            grid-size: 1;
            grid-rows: 1fr;
        }
        DataTable {
            height: 100%;
            width: 100%;
        }
        #status-bar {
            dock: bottom;
            height: 1;
            background: $boost;
            color: $text;
        }
        #filter-bar {
            dock: top;
            height: 1;
            background: $accent;
            color: $surface;
            visibility: hidden;
        }
        #search-bar {
            dock: bottom;
            height: 1;
            background: $accent;
            color: $surface;
            visibility: hidden;
        }
    """

    def __init__(self, path: str | None = None, path2: str | None = None) -> None:
        super().__init__()
        self._path: Path | None = Path(path) if path else None
        self._path2: Path | None = Path(path2) if path2 else None
        self._df: pd.DataFrame | None = None
        self._dt: DataTable | None = None
        self._types: dict[str, str] = {}
        self._col_names: list[str] = []
        self._all_columns: list[str] = []
        self._col_keys: list = []
        self._row_keys: list = []
        self._edited: dict[tuple[int, int], str] = {}
        self._origins: dict[tuple[int, int], str] = {}
        self._raw: dict[tuple[int, int], str] = {}
        self._parquet_file: pq.ParquetFile | None = None
        self._zst_reader: LazyJsonlReader | None = None
        self._num_rows: int = 0
        self._lazy: bool = False
        self._visible_rows: int = 30
        self._filter_active: bool = False
        self._filter_df: pd.DataFrame | None = None
        self._search_pattern: str | None = None
        self._search_matches: list[tuple[int, int]] = []
        self._search_cursor: int = -1
        self._schema: pa.Schema | None = None
        self._status_bar: Label | None = None
        self._filter_bar: FilterBar | None = None
        self._footer: Footer | None = None
        self._search_bar: Label | None = None
        self._deleted_rows: set[int] = set()
        self._hidden_cols: set[str] = set()
        self._sort_col: str | None = None
        self._sort_asc: bool = True
        self._clipboard: str = ""
        self._tabs: list[dict] = []
        self._active_tab: int = -1
        self._startup_df: pd.DataFrame | None = None
        self._startup_schema: pa.Schema | None = None

    # -- mount ---------------------------------------------------------------
    async def on_mount(self) -> None:
        if self._path2 is not None:
            self.push_screen(
                DiffScreen(str(self._path), str(self._path2)),
            )
            return

        if self._path is None:
            self.push_screen(RecentFilesScreen(), callback=self._on_file_chosen)
            return

        # If startup state was provided (from pipeline), use it
        if self._startup_df is not None:
            self._df = self._startup_df
            self._schema = self._startup_schema
            self._num_rows = len(self._df)
            if _is_zst_file(str(self._path)):
                self._zst_reader = self._startup_schema if isinstance(self._startup_schema, LazyJsonlReader) else None
            else:
                self._parquet_file = pq.ParquetFile(str(self._path))
            await self._populate_table(self._df)
            self._update_status()
            self._startup_df = None
            self._startup_schema = None
            return

        target = self._path
        if target.is_dir():
            self.push_screen(
                DirBrowserScreen(str(target)),
                callback=self._on_file_chosen,
            )
            return

        if _is_zst_file(str(target)):
            await self._open_zst(str(target))
        else:
            await self._open_parquet(str(target))

    async def _on_file_chosen(self, path: str | None) -> None:
        if path:
            self._path = Path(path)
            if self._path.is_dir():
                self.push_screen(
                    DirBrowserScreen(str(self._path)),
                    callback=self._on_file_chosen,
                )
            else:
                await self._clear_widgets()
                self._reset_state()
                if _is_zst_file(path):
                    await self._open_zst(path)
                else:
                    await self._open_parquet(path)

    async def _clear_widgets(self) -> None:
        screen = self.screen
        to_remove = []
        for widget_id in ("filter-bar", "status-bar", "search-bar", "search-prompt"):
            for w in screen.query(f"#{widget_id}"):
                to_remove.append(w)
        for w in screen.query(Footer):
            to_remove.append(w)
        for w in screen.query(DataTable):
            to_remove.append(w)
        for w in to_remove:
            await w.remove()

    def _reset_state(self) -> None:
        self._df = None
        self._dt = None
        self._types = {}
        self._col_names = []
        self._all_columns = []
        self._col_keys = []
        self._row_keys = []
        self._edited = {}
        self._origins = {}
        self._raw = {}
        self._parquet_file = None
        if self._zst_reader is not None:
            self._zst_reader.close()
        self._zst_reader = None
        self._num_rows = 0
        self._lazy = False
        self._filter_active = False
        self._filter_df = None
        self._search_pattern = None
        self._search_matches = []
        self._search_cursor = -1
        self._schema = None
        self._status_bar = None
        self._filter_bar = None
        self._footer = None
        self._search_bar = None
        self._deleted_rows = set()
        self._hidden_cols = set()
        self._sort_col = None
        self._sort_asc = True
        self._clipboard = ""
        self._tabs = []
        self._active_tab = -1

    async def _open_parquet(self, path: str) -> None:
        _add_to_history(path)
        self._path = Path(path)

        meta = pq.read_metadata(path)
        self._num_rows = meta.num_rows
        self._parquet_file = pq.ParquetFile(path)
        self._schema = self._parquet_file.schema_arrow

        self._lazy = self._num_rows > 5000

        if self._lazy:
            self._load_lazy_initial()
        else:
            self._df = pq.read_table(path).to_pandas()
            await self._populate_table(self._df)
        self._update_status()

    async def _open_zst(self, path: str) -> None:
        _add_to_history(path)
        self._path = Path(path)

        if zstd is None:
            self._notify_zstd_missing()
            return

        self._zst_reader = LazyJsonlReader(path)
        self._zst_reader._ensure_indexed()
        self._num_rows = self._zst_reader.num_rows
        self._all_columns = self._zst_reader.columns

        self._lazy = True

        # Load initial chunk
        self._df = self._zst_reader.get_row_range(0, min(self._visible_rows, self._num_rows))
        self._col_names = self._all_columns
        self._types = {c: self._zst_reader.dtypes.get(c, "object") for c in self._all_columns}

        dt = PlainDataTable(show_cursor=True, zebra_stripes=True)
        self._col_keys = dt.add_columns(*self._all_columns)
        self._dt = dt
        self.mount(dt)

        self._mount_bars()
        self._render_zst_visible_rows(0, min(self._visible_rows, self._num_rows))
        self._update_status()

    def _notify_zstd_missing(self) -> None:
        try:
            self.exit()
        except Exception:
            pass
        print("Error: zstandard module not installed. Install with: pip install zstandard", file=sys.stderr)
        sys.exit(1)

    def _load_lazy_initial(self) -> None:
        if self._parquet_file is None:
            return
        table = self._parquet_file.read_row_group(0)
        self._df = table.to_pandas()

        col_names = list(self._df.columns)
        self._col_names = col_names
        self._types = {c: str(t) for c, t in self._df.dtypes.to_dict().items()}

        dt = PlainDataTable(show_cursor=True, zebra_stripes=True)
        self._col_keys = dt.add_columns(*col_names)
        self._dt = dt
        self.mount(dt)

        self._mount_bars()
        self._render_visible_rows(0, min(self._visible_rows, self._num_rows))

    def _render_zst_visible_rows(self, start: int, end: int) -> None:
        if self._zst_reader is None:
            return
        dt = self._dt
        if dt is None:
            return
        df_chunk = self._zst_reader.get_row_range(start, end)
        dt.clear(columns=True)
        self._col_keys = dt.add_columns(*self._all_columns)
        rows_data = [
            [self._fmt(v) for v in df_chunk.iloc[idx - start].values]
            for idx in range(start, min(end, self._num_rows))
        ]
        self._row_keys = list(dt.add_rows(rows_data))
        for ri in range(start, min(end, self._num_rows)):
            for ci, v in enumerate(df_chunk.iloc[ri - start].values):
                self._raw[(ri, ci)] = self._full(v)

    def _mount_bars(self) -> None:
        filter_bar = FilterBar(id="filter-bar", placeholder="Filter (col == value)... press f to toggle")
        self.mount(filter_bar)
        self._filter_bar = filter_bar

        status_bar = Label(id="status-bar")
        self.mount(status_bar)
        self._status_bar = status_bar

        footer = Footer()
        self.mount(footer)
        self._footer = footer

    def _render_visible_rows(self, start: int, end: int) -> None:
        if self._zst_reader is not None:
            self._render_zst_visible_rows(start, end)
            return
        if self._lazy and self._parquet_file is None:
            return
        dt = self._dt
        if dt is None:
            return

        if self._lazy:
            df_chunk = self._get_row_range(start, end)
            dt.clear(columns=True)
            self._col_keys = dt.add_columns(*self._col_names)
            rows_data = [
                [self._fmt(v) for v in df_chunk.iloc[idx - start].values]
                for idx in range(start, min(end, self._num_rows))
            ]
            self._row_keys = list(dt.add_rows(rows_data))
            for ri in range(start, min(end, self._num_rows)):
                for ci, v in enumerate(df_chunk.iloc[ri - start].values):
                    self._raw[(ri, ci)] = self._full(v)
        else:
            if self._df is None:
                return
            rows_data = [
                [self._fmt(v) for v in self._df.iloc[idx].values]
                for idx in range(start, min(end, len(self._df)))
            ]
            dt.clear(columns=True)
            self._col_keys = dt.add_columns(*self._col_names)
            self._row_keys = list(dt.add_rows(rows_data))

    def _get_row_range(self, start: int, end: int) -> pd.DataFrame:
        if self._zst_reader is not None:
            return self._zst_reader.get_row_range(start, end)
        if self._parquet_file is None:
            return pd.DataFrame()

        chunks = []
        rg = self._parquet_file.metadata
        row_offset = 0
        for i in range(rg.num_row_groups):
            rg_size = rg.row_group(i).num_rows
            rg_start = row_offset
            rg_end = row_offset + rg_size
            if rg_end <= start:
                row_offset = rg_end
                continue
            if rg_start >= end:
                break
            read_start = max(0, start - row_offset)
            read_end = min(rg_size, end - row_offset)
            table = self._parquet_file.read_row_group(
                i, columns=self._col_names, use_threads=True,
            )
            table = table.slice(read_start, read_end - read_start)
            chunks.append(table.to_pandas())
            row_offset = rg_end

        if chunks:
            return pd.concat(chunks, ignore_index=True)
        return pd.DataFrame(columns=self._col_names)

    async def _populate_table(self, df: pd.DataFrame, clear: bool = True) -> None:
        if clear:
            if self._dt is not None:
                self._dt.clear(columns=True)
            else:
                await self._clear_widgets()
        col_names = list(df.columns)
        self._col_names = col_names
        self._types = {c: str(t) for c, t in df.dtypes.to_dict().items()}

        if self._dt is None:
            dt = PlainDataTable(show_cursor=True, zebra_stripes=True)
            self._col_keys = dt.add_columns(*col_names)
        else:
            dt = self._dt
            for ckey in list(self._col_keys):
                try:
                    dt.remove_column(ckey)
                except Exception:
                    pass
            self._col_keys = dt.add_columns(*col_names)

        self._row_keys = list(
            dt.add_rows([
                [self._fmt(v) for v in df.iloc[idx].values]
                for idx in range(len(df))
            ])
        )

        self._raw.clear()
        for ri in range(len(df)):
            for ci, v in enumerate(df.iloc[ri].values):
                self._raw[(ri, ci)] = self._full(v)

        if self._dt is None:
            self._dt = dt
            self.mount(dt)
            self._mount_bars()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _is_container(v) -> bool:
        return isinstance(v, (list, pd.Series)) or hasattr(v, 'shape')

    @staticmethod
    def _fmt(v) -> str:
        if hasattr(v, "__len__") and not isinstance(v, (str, bytes)):
            if len(v) == 0:
                return ""
            v = str(v)
        if pd.isna(v):
            return ""
        s = str(v)
        return escape(s[:200] if len(s) > 200 else s)

    @staticmethod
    def _full(v) -> str:
        if ParquetReader._is_container(v):
            return str(v)
        if pd.isna(v):
            return ""
        return str(v)

    def _update_status(self) -> None:
        dt = self._dt
        if dt is None:
            return
        nr = self._num_rows if self._lazy else (len(self._df) if self._df is not None else dt.row_count)
        nc = len(self._col_names)
        row = dt.cursor_row
        col = dt.cursor_column

        parts = []
        if self._path:
            parts.append(self._path.name)
        parts.append(f"{nr} rows")
        parts.append(f"{nc} cols")

        if row is not None and col is not None and col < nc:
            parts.append(f"[{row+1}/{nr}]")
            col_name = self._col_names[col]
            parts.append(col_name)

            if self._filter_active:
                parts.append("FILTERED")

            parts.append(f"{len(self._edited)} edit(s)")

            if self._df is not None and col < len(self._df.columns):
                col_dtype = self._types.get(col_name, "")
                if any(t in col_dtype for t in ("int", "float")):
                    series = self._filter_df[col_name] if self._filter_active and self._filter_df is not None else self._df[col_name]
                    non_null = series.dropna()
                    if len(non_null) > 0:
                        mean_val = f"{non_null.mean():.2f}"
                        min_val = f"{non_null.min():.2f}"
                        max_val = f"{non_null.max():.2f}"
                        null_count = int(series.isna().sum())
                        stats_str = f"Mean:{mean_val} Min:{min_val} Max:{max_val} Nulls:{null_count}"
                        parts.append(stats_str)

        t = " | ".join(parts)

        try:
            self.query_one("#status-bar", Label).update(t)
        except Exception:
            pass

    # -- navigation ----------------------------------------------------------

    def action_down(self) -> None:
        self._dt.move_cursor(row=(self._dt.cursor_row or 0) + 1)
        self._on_cursor_moved()

    def action_up(self) -> None:
        self._dt.move_cursor(row=(self._dt.cursor_row or 0) - 1)
        self._on_cursor_moved()

    def action_left(self) -> None:
        self._dt.move_cursor(column=(self._dt.cursor_column or 0) - 1)
        self._on_cursor_moved()

    def action_right(self) -> None:
        self._dt.move_cursor(column=(self._dt.cursor_column or 0) + 1)
        self._on_cursor_moved()

    def action_home(self) -> None:
        if self._dt.row_count:
            self._dt.cursor_coordinate = (0, 0)
        self._on_cursor_moved()

    def action_end(self) -> None:
        nr = self._num_rows if self._lazy else (len(self._df) if self._df is not None else 0)
        col = self._dt.cursor_column or 0
        if nr:
            self._dt.cursor_coordinate = (nr - 1, col)
        self._on_cursor_moved()

    def action_page_down(self) -> None:
        self._dt.scroll_page_down()
        if self._lazy:
            self._refetch_visible()
        self._on_cursor_moved()

    def action_page_up(self) -> None:
        self._dt.scroll_page_up()
        if self._lazy:
            self._refetch_visible()
        self._on_cursor_moved()

    def _on_cursor_moved(self) -> None:
        self._update_status()

    def _refetch_visible(self) -> None:
        if not self._lazy or self._dt is None:
            return
        try:
            viewport_row = self._dt.offset_row
        except Exception:
            viewport_row = self._dt.cursor_row or 0
        start = viewport_row
        end = start + self._visible_rows
        if self._zst_reader is not None:
            self._render_zst_visible_rows(start, end)
        else:
            self._render_visible_rows(start, end)

    # -- editing -------------------------------------------------------------

    def _get_cell(self) -> tuple[int, int, str, str, str] | None:
        dt = self._dt
        row = dt.cursor_row
        col = dt.cursor_column
        if row is None or col is None:
            return None
        cell = dt.get_cell_at((row, col))
        if hasattr(cell, "data"):
            display = str(cell.data) if cell.data is not None else ""
        else:
            display = str(cell) if cell is not None else ""
        col_name = self._col_names[col] if col < len(self._col_names) else "?"
        return row, col, display, str(row), col_name

    def _open_edit(self, append: bool = False) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        key = (ri, ci)
        current = self._edited.get(key, self._raw.get(key, display))
        original = self._origins.get(key, self._raw.get(key, display))
        self.push_screen(
            EditScreen(self, ri, ci, current, original, rk, ck, append),
            callback=self._edit_callback,
        )

    def action_edit(self) -> None:
        self._open_edit(append=False)

    def action_edit_append(self) -> None:
        self._open_edit(append=True)

    def action_view_cell(self) -> None:
        dt = self._dt
        if dt is None or dt.cursor_row is None or dt.cursor_column is None:
            return
        row = dt.cursor_row
        col = dt.cursor_column
        text_str = self._raw.get((row, col), "")
        self.push_screen(ViewCellScreen(text_str))

    def edit_cell(
        self,
        row_idx: int,
        col_idx: int,
        row_key: str,
        col_key: str,
        new_value: str,
        original: str,
    ) -> None:
        key = (row_idx, col_idx)
        if key not in self._origins:
            self._origins[key] = original
        self._edited[key] = new_value
        dt = self._dt
        rkey = self._row_keys[row_idx]
        ckey = self._col_keys[col_idx]
        dt.update_cell(rkey, ckey, new_value)
        self._update_status()

    def _edit_callback(self, result: bool) -> None:
        self._update_status()

    # -- save ----------------------------------------------------------------

    def action_save(self) -> None:
        if not self._edited and not self._deleted_rows:
            self.notify("[green]No changes to save.[/green]")
            return

        if self._zst_reader is not None:
            df = self._zst_reader.get_row_range(0, self._num_rows)
        elif self._lazy:
            df = self._parquet_file.to_pandas() if self._parquet_file is not None else None
        else:
            df = self._df.copy() if self._df is not None else None

        if df is None:
            self.notify("[red]Cannot save: no data loaded.[/red]")
            return

        if self._deleted_rows:
            df = df.drop(index=list(self._deleted_rows)).reset_index(drop=True)

        for (ri, ci), nv in self._edited.items():
            ci_actual = min(ci, len(self._col_names) - 1)
            ck = self._col_names[ci_actual]
            try:
                cv = self._convert(nv, self._types.get(ck, "object"))
                df.iloc[ri, ci_actual] = cv
            except (ValueError, TypeError, IndexError):
                pass

        if _is_zst_file(str(self._path)):
            out = self._path.with_suffix(".edited.jsonl")
            df.to_json(str(out), orient="records", lines=True, force_ascii=False)
            self.notify(f"[green]Saved JSONL to {out.name}[/green]")
        else:
            out = self._path.with_stem(self._path.stem + "_edited")
            pq.write_table(pa.Table.from_pandas(df), str(out))
            self.notify(f"[green]Saved to {out.name}[/green]")
        self._edited.clear()
        self._origins.clear()
        self._deleted_rows.clear()
        self._update_status()

    @staticmethod
    def _convert(value: str, dtype: str):
        if not value:
            return pd.NA
        if "int" in dtype:
            return int(value)
        if "float" in dtype:
            return float(value)
        if "bool" in dtype:
            return value.lower() in ("true", "1", "yes")
        if "datetime" in dtype:
            return pd.Timestamp(value)
        return value

    # -- export --------------------------------------------------------------

    def action_export(self) -> None:
        self.push_screen(ExportScreen(), callback=self._do_export)

    def _do_export(self, fmt: str | None) -> None:
        if fmt is None:
            return

        if self._zst_reader is not None:
            df = self._zst_reader.get_row_range(0, self._num_rows)
        elif self._lazy and self._parquet_file is not None:
            df = self._parquet_file.to_pandas()
        else:
            df = self._df.copy() if self._df is not None else None

        if df is None:
            self.notify("[red]No data to export.[/red]")
            return

        for (ri, ci), nv in self._edited.items():
            ci_actual = min(ci, len(self._col_names) - 1)
            ck = self._col_names[ci_actual]
            try:
                cv = self._convert(nv, self._types.get(ck, "object"))
                df.iloc[ri, ci_actual] = cv
            except (ValueError, TypeError, IndexError):
                pass

        stem = self._path.stem if self._path else "export"
        if fmt == "csv":
            out = self._path.parent / f"{stem}.csv"
            df.to_csv(str(out), index=False)
            self.notify(f"[green]CSV exported to {out.name}[/green]")
        elif fmt == "excel":
            out = self._path.parent / f"{stem}.xlsx"
            try:
                df.to_excel(str(out), index=False, engine="openpyxl")
                self.notify(f"[green]Excel exported to {out.name}[/green]")
            except ImportError:
                self.notify("[yellow]Install openpyxl: pip install openpyxl[/yellow]")
        elif fmt == "parquet":
            out = self._path.parent / f"{stem}_export.parquet"
            pq.write_table(pa.Table.from_pandas(df), str(out))
            self.notify(f"[green]Parquet exported to {out.name}[/green]")

    # -- open file -----------------------------------------------------------

    def action_open_file(self) -> None:
        initial = str(self._path.parent) if self._path else "."
        self.push_screen(
            FileBrowserScreen(initial),
            callback=lambda path: self._on_open_file(path),
        )

    async def _on_open_file(self, path: Optional[str]) -> None:
        if path and Path(path).exists():
            self._path = Path(path)
            _add_to_history(str(self._path))
            await self._clear_widgets()
            self._reset_state()
            if _is_zst_file(path):
                await self._open_zst(path)
            else:
                await self._open_parquet(path)

    # -- step execution (unifies CLI and TUI) --------------------------------

    def _build_state(self) -> PipelineState:
        return PipelineState(
            df=self._filter_df if self._filter_active else self._df,
            schema=self._schema if self._zst_reader is None else self._zst_reader,
            path=self._path,
            hidden_cols=self._hidden_cols,
            sort_col=self._sort_col,
            sort_asc=self._sort_asc,
            clipboard=self._clipboard,
        )

    async def _run_step(self, step: Step) -> StepResult:
        handler = _STEP_MAP.get(step.name)
        if handler is None:
            self.notify(f"[red]Unknown step: {step.name}[/red]")
            return StepResult(message=f"Unknown step: {step.name}")

        state = self._build_state()
        state.args = dict(step.args)
        result = handler(state)

        if result.df is not None and step.name not in ("schema", "stats", "search", "shell", "yank"):
            self._df = result.df
            self._filter_df = result.df
            await self._populate_table(result.df)
            self._update_status()

        if result.message:
            self.notify(f"[green]{step.name}:[/green] {result.message[:150]}")
        if result.yanked:
            self._clipboard = result.yanked

        if step.name == "hide" and state.args.get("column"):
            col = state.args["column"]
            if col in self._hidden_cols:
                self._hidden_cols.discard(col)
                try:
                    for ci, ck in enumerate(self._col_keys):
                        if self._col_names[ci] == col:
                            self._dt.show_column(ck)
                            break
                except Exception:
                    pass

        return result

    async def _run_steps(self, specs: list[str]) -> None:
        for spec in specs:
            step = Step.parse_spec(spec)
            await self._run_step(step)

    # -- schema viewer -------------------------------------------------------

    def action_schema(self) -> None:
        if self._zst_reader is not None:
            buf = f"File: {self._path}\n"
            buf += f"Columns: {len(self._all_columns)}\n"
            buf += f"Rows: {self._num_rows}\n\n"
            buf += "Column Details:\n"
            for i, col in enumerate(self._all_columns):
                buf += f"  {i+1}. {col} : {self._types.get(col, 'object')}\n"
            self.notify(buf[:200])
            return
        if self._schema is None:
            self.notify("[yellow]No schema loaded.[/yellow]")
            return
        self.push_screen(SchemaScreen(str(self._path), self._schema))

    # -- search --------------------------------------------------------------

    def action_search(self) -> None:
        prompt = SearchPrompt()
        self.mount(prompt)

    def on_search_prompt_search_submitted(self, event: SearchPrompt.SearchSubmitted) -> None:
        if event.value.strip():
            self._do_search(event.value.strip())

    def _do_search(self, pattern: str) -> None:
        if self._dt is None:
            return
        self._search_pattern = pattern
        self._search_matches = []
        self._search_cursor = -1

        df = self._filter_df if self._filter_active else self._df
        if df is None:
            self.notify("[yellow]No data to search.[/yellow]")
            return

        pattern_lower = pattern.lower()
        for ri in range(len(df)):
            for ci, col_name in enumerate(self._col_names):
                val = df.iloc[ri, ci]
                if ParquetReader._is_container(val):
                    if val.size > 0 and pattern_lower in str(val).lower():
                        self._search_matches.append((ri, ci))
                elif val is not None and not pd.isna(val):
                    if pattern_lower in str(val).lower():
                        self._search_matches.append((ri, ci))

        if self._search_matches:
            self._search_cursor = 0
            r, c = self._search_matches[0]
            self._dt.cursor_coordinate = (r, c)
            self._update_status()
            self._highlight_matches()
            self._show_search_bar(pattern, len(self._search_matches))
            self._dt.focus()
        else:
            self.notify("[yellow]No matches found.[/yellow]")
            self._dt.focus()

    def _highlight_matches(self) -> None:
        if self._dt is None:
            return
        match_set = set(self._search_matches)
        for ri in range(self._dt.row_count):
            if ri >= len(self._row_keys):
                break
            for ci in range(len(self._col_keys)):
                rkey = self._row_keys[ri]
                ckey = self._col_keys[ci]
                if (ri, ci) in match_set:
                    try:
                        self._dt.styles_cell(rkey, ckey, highlight=True)
                    except Exception:
                        pass

    def _show_search_bar(self, pattern: str, count: int) -> None:
        try:
            bar = self.query_one("#search-bar", Label)
            bar.update(f"/{pattern}  {count} matches  n/N navigate")
            bar.styles.visibility = "visible"
            self._search_bar = bar
        except Exception:
            label = Label(f"/{pattern}  {count} matches  n/N navigate", id="search-bar")
            self.mount(label)
            self._search_bar = label

    def action_search_next(self) -> None:
        if not self._search_matches:
            return
        self._search_cursor = (self._search_cursor + 1) % len(self._search_matches)
        r, c = self._search_matches[self._search_cursor]
        self._dt.cursor_coordinate = (r, c)
        self._update_status()

    def action_search_prev(self) -> None:
        if not self._search_matches:
            return
        self._search_cursor = (self._search_cursor - 1) % len(self._search_matches)
        r, c = self._search_matches[self._search_cursor]
        self._dt.cursor_coordinate = (r, c)
        self._update_status()

    # -- filter --------------------------------------------------------------

    async def action_filter(self) -> None:
        bar = self.query_one("#filter-bar", FilterBar)
        if bar.styles.visibility == "visible":
            bar.styles.visibility = "hidden"
            self._filter_active = False
            self._filter_df = None
            if self._df is not None:
                await self._populate_table(self._df)
            self._update_status()
        else:
            bar.styles.visibility = "visible"
            bar.focus()

    async def on_filter_bar_filter_changed(self, event: FilterBar.FilterChanged) -> None:
        bar = self.query_one("#filter-bar", FilterBar)
        query_str = bar.value.strip()
        if not query_str:
            if self._filter_active:
                self._filter_active = False
                self._filter_df = None
                if self._df is not None:
                    await self._populate_table(self._df)
                self._update_status()
            return

        df = self._df
        if df is None:
            return

        try:
            if "!=" in query_str:
                col, val = query_str.split("!=", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif ">=" in query_str:
                col, val = query_str.split(">=", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif "<=" in query_str:
                col, val = query_str.split("<=", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif ">" in query_str:
                col, val = query_str.split(">", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif "<" in query_str:
                col, val = query_str.split("<", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif "==" in query_str:
                col, val = query_str.split("==", 1)
                col, val = col.strip(), val.strip().strip("'\"")
            elif " " in query_str:
                col, val = query_str.split(None, 1)
                col, val = col.strip(), val.strip().strip("'\"")
            else:
                col = query_str
                val = None

            if col not in df.columns:
                self.notify(f"[red]Column '{col}' not found.[/red]")
                return

            if val is not None:
                filtered = df[df[col].astype(str).str.contains(val, case=False, na=False)]
            else:
                filtered = df[df[col].notna()]

            self._filter_active = True
            self._filter_df = filtered
            await self._populate_table(filtered)
            self._update_status()
        except Exception as e:
            self.notify(f"[red]Filter error: {e}[/red]")

    # -- stats ---------------------------------------------------------------

    def action_stats(self) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        col_name = ck
        df = self._filter_df if self._filter_active else self._df
        if df is None or col_name not in df.columns:
            return

        series = df[col_name]
        lines = [f"[bold]Column:[/bold] {col_name}"]
        lines.append(f"[bold]Type:[/bold] {self._types.get(col_name, '?')}")
        lines.append(f"[bold]Non-null:[/bold] {int(series.notna().sum())}")
        lines.append(f"[bold]Nulls:[/bold] {int(series.isna().sum())}")

        col_dtype = self._types.get(col_name, "")
        if any(t in col_dtype for t in ("int", "float")):
            non_null = series.dropna()
            if len(non_null) > 0:
                lines.append(f"[bold]Mean:[/bold] {non_null.mean():.4f}")
                lines.append(f"[bold]Std:[/bold] {non_null.std():.4f}")
                lines.append(f"[bold]Min:[/bold] {non_null.min():.4f}")
                lines.append(f"[bold]25%:[/bold] {non_null.quantile(0.25):.4f}")
                lines.append(f"[bold]50%:[/bold] {non_null.quantile(0.5):.4f}")
                lines.append(f"[bold]75%:[/bold] {non_null.quantile(0.75):.4f}")
                lines.append(f"[bold]Max:[/bold] {non_null.max():.4f}")
        elif "object" in col_dtype or "string" in col_dtype:
            lines.append(f"[bold]Unique:[/bold] {series.nunique()}")
            top = series.dropna().value_counts().head(5)
            for val, cnt in top.items():
                lines.append(f"  {val}: {cnt}")
        elif "bool" in col_dtype:
            non_null = series.dropna()
            lines.append(f"[bold]True:[/bold] {int(non_null.sum())}")
            lines.append(f"[bold]False:[/bold] {int((~non_null).sum())}")

        self.notify(" | ".join(lines))

    # -- yank (copy cell to clipboard) ---------------------------------------

    @staticmethod
    def _copy_to_clipboard(text: str) -> bool:
        """Copy text to clipboard, trying multiple methods. Returns True on success."""
        import subprocess
        import base64
        import os

        def _try_cmd(cmd: list[str]) -> bool:
            try:
                subprocess.run(cmd, input=text.encode(), capture_output=True, check=True)
                return True
            except Exception:
                return False

        def _osc52(device: str) -> bool:
            try:
                b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
                osc = f"\x1b]52;c;{b64}\x07"
                with open(device, "w") as tty:
                    tty.write(osc)
                    tty.flush()
                return True
            except Exception:
                return False

        # 1. xclip (Linux X11)
        if os.environ.get("DISPLAY") and _try_cmd(["xclip", "-selection", "clipboard"]):
            return True

        # 2. wl-copy (Linux Wayland)
        if os.environ.get("WAYLAND_DISPLAY") and _try_cmd(["wl-copy"]):
            return True

        # 3. pbcopy (macOS)
        if _try_cmd(["pbcopy"]):
            return True

        # 4. clip (Windows CMD)
        if _try_cmd(["clip"]):
            return True

        # 5. OSC 52 to the correct terminal device (bypasses Textual's captured stdout)
        tty_candidates = []

        # If inside tmux, get the client's tty (outer terminal, not the pane)
        if os.environ.get("TMUX"):
            try:
                result = subprocess.run(
                    ["tmux", "display-message", "-p", "#{client_tty}"],
                    capture_output=True, text=True
                )
                client_tty = result.stdout.strip()
                if client_tty.startswith("/dev/"):
                    tty_candidates.append(client_tty)
            except Exception:
                pass

        # SSH_TTY (the SSH client's terminal)
        if os.environ.get("SSH_TTY"):
            tty_candidates.append(os.environ["SSH_TTY"])

        # /dev/tty (controlling terminal)
        tty_candidates.append("/dev/tty")

        # Own stdin device from /proc
        try:
            own_fd = os.readlink("/proc/self/fd/0")
            if own_fd.startswith("/dev/"):
                tty_candidates.append(own_fd)
        except Exception:
            pass

        # Deduplicate preserving order
        seen = set()
        for dev in tty_candidates:
            if dev not in seen:
                seen.add(dev)
                if _osc52(dev):
                    return True

        return False

    def action_yank_cell(self) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        self._clipboard = self._raw.get((ri, ci), display)
        if self._copy_to_clipboard(self._clipboard):
            self.notify(f"[green]Yanked:[/green] {self._clipboard[:80]}")
        else:
            self.notify("[red]Yank failed:[/red] no clipboard backend available")

    # -- hide/show column (H) ------------------------------------------------

    def action_hide_column(self) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        col_name = ck
        if col_name in self._hidden_cols:
            self._hidden_cols.discard(col_name)
            try:
                ckey = self._col_keys[ci] if ci < len(self._col_keys) else None
                if ckey is not None:
                    self._dt.show_column(ckey)
            except Exception:
                pass
            self.notify(f"[green]Showd[/green] column {col_name}")
        else:
            self._hidden_cols.add(col_name)
            try:
                ckey = self._col_keys[ci] if ci < len(self._col_keys) else None
                if ckey is not None:
                    self._dt.hide_column(ckey)
            except Exception:
                pass
            self.notify(f"[yellow]Hidden[/yellow] column {col_name}")

    # -- sort column (s) -----------------------------------------------------

    async def action_sort_column(self) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        col_name = ck
        if self._sort_col == col_name:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_name
            self._sort_asc = True
        df = self._df
        if df is None:
            return
        df = df.copy()
        if df.empty:
            return
        if self._is_container(df[col_name].iloc[0]):
            df["_sort_key"] = df[col_name].apply(
                lambda x: str(x) if (hasattr(x, 'size') and x.size > 0) else "",
            )
            df = df.sort_values("_sort_key", ascending=self._sort_asc)
            df.drop(columns=["_sort_key"], inplace=True)
        else:
            df = df.sort_values(col_name, ascending=self._sort_asc)
        df = df.reset_index(drop=True)
        self._df = df
        await self._populate_table(df)
        self._update_status()
        self.notify(f"[cyan]Sorted[/cyan] {col_name} {'↑' if self._sort_asc else '↓'}")

    # -- delete row (dd) -----------------------------------------------------

    def action_delete_row(self) -> None:
        dt = self._dt
        if dt is None or dt.cursor_row is None:
            return
        row = dt.cursor_row
        self._deleted_rows.add(row)
        try:
            rkey = self._row_keys[row] if row < len(self._row_keys) else None
            if rkey is not None:
                self._dt.hide_row(rkey)
        except Exception:
            pass
        self.notify(f"[red]Marked row {row+1}[/red] for deletion")

    # -- add row (O) ---------------------------------------------------------

    async def action_add_row(self) -> None:
        if self._df is None:
            return
        new_row = pd.DataFrame({c: [pd.NA] for c in self._col_names})
        self._df = pd.concat([self._df, new_row], ignore_index=True)
        self._num_rows = len(self._df)
        await self._populate_table(self._df)
        self._update_status()
        last = len(self._df) - 1
        self._dt.cursor_coordinate = (last, 0)
        self.notify(f"[green]Added row {last+1}[/green]")

    # -- SQL query mode (:) --------------------------------------------------

    class SqlPrompt(Container):
        class SqlSubmitted(Message):
            def __init__(self, value: str) -> None:
                super().__init__()
                self.value = value

        CSS = """
            height: 1;
            width: 100%;
            background: $accent;
            #sql-prefix {
                width: 3;
                color: $surface;
                background: $accent;
            }
            #sql-input {
                width: 1fr;
            }
        """

        def __init__(self) -> None:
            super().__init__(
                Static(":", id="sql-prefix"),
                Input(id="sql-input", placeholder="DuckDB SQL query..."),
                id="sql-prompt",
            )

        def on_mount(self) -> None:
            self.query_one("#sql-input", Input).focus()

        def on_key(self, event) -> None:
            inp = self.query_one("#sql-input", Input)
            if event.key == "enter":
                event.prevent_default()
                self.post_message(SqlPrompt.SqlSubmitted(inp.value))
                self.remove()
            elif event.key == "escape":
                event.prevent_default()
                self.remove()

    def action_sql_query(self) -> None:
        self.mount(SqlPrompt())

    async def on_sql_prompt_sql_submitted(self, event: SqlPrompt.SqlSubmitted) -> None:
        query = event.value.strip()
        if not query:
            return
        try:
            import duckdb as dd
            df = self._filter_df if self._filter_active else self._df
            if df is None:
                self.notify("[yellow]No data for SQL query.[/yellow]")
                return
            rel = dd.table(df)
            result = dd.sql(query).arrow().to_pandas()
            self._filter_active = True
            self._filter_df = result
            old_cols = self._col_names
            await self._populate_table(result)
            self._col_names = old_cols
            self._update_status()
            self.notify(f"[green]SQL result:[/green] {len(result)} rows")
        except ImportError:
            self.notify("[yellow]Install duckdb: pip install duckdb[/yellow]")
        except Exception as e:
            self.notify(f"[red]SQL error:[/red] {e}")

    # -- multi-file tabs (Tab/gt/gT) -----------------------------------------

    def _save_tab_state(self) -> None:
        if self._path is None:
            return
        if self._active_tab < len(self._tabs):
            tab = self._tabs[self._active_tab]
            tab["path"] = str(self._path)
            tab["df"] = self._df.copy() if self._df is not None else None
            tab["active"] = True
            return
        self._tabs.append({
            "path": str(self._path),
            "df": self._df.copy() if self._df is not None else None,
            "active": True,
        })
        self._active_tab = len(self._tabs) - 1

    async def _load_tab_state(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            tab = self._tabs[index]
            for t in self._tabs:
                t["active"] = False
            tab["active"] = True
            self._active_tab = index
            path = tab["path"]
            self._path = Path(path)
            self._df = tab.get("df")
            if self._df is not None:
                self._num_rows = len(self._df)
                self._parquet_file = pq.ParquetFile(path)
                self._schema = self._parquet_file.schema_arrow
                await self._populate_table(self._df, clear=False)
                self._update_status()

    async def action_next_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._save_tab_state()
        next_idx = (self._active_tab + 1) % len(self._tabs)
        await self._load_tab_state(next_idx)

    async def action_prev_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._save_tab_state()
        prev_idx = (self._active_tab - 1) % len(self._tabs)
        await self._load_tab_state(prev_idx)

    # -- quit ----------------------------------------------------------------

    def action_quit(self) -> None:
        if self._edited:
            self.notify("[yellow]Unsaved changes — press 'w' to save first.[/yellow]")
        else:
            self.exit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pqr",
        description="Parquet viewer & editor — vim-like TUI for .parquet files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  pqr data.parquet                          # Open in TUI
  pqr data.parquet --schema                 # Print schema
  pqr data.parquet --step "filter:price > 10" --schema  # Filter then schema
  pqr data.parquet --step "sql:SELECT * FROM df LIMIT 5"  # SQL query
  pqr data.parquet --step "sort:column=price" --export  # Sort then export
  pqr data.parquet --step "yank:column=name;row=0"       # Yank cell to clipboard

Built-in steps (use --step):
  schema          Print schema and column metadata
  yank            Copy cell/column to clipboard (needs column=, optional row=)
  search          Search text (expr=keyword)
  filter          Filter rows (expr=pandas query string)
  sort            Sort by column (column=name, desc=true/false)
  hide            Hide column (column=name)
  sql             Run SQL query (needs duckdb) (expr=SELECT ...)
  stats           Column statistics (optional column=name)
  delete-row      Delete row (row=index)
  export          Export to CSV/JSON/parquet (format=csv, output=file)

Custom steps:
  python:EXPR     Evaluate Python expression (EXPR has df, pd available)
  shell:CMD       Run shell command (data piped via stdin as CSV)

Custom shortcuts:
  Store in ~/.config/pqr/shortcuts.toml:
    [shortcuts.summary]
    steps = ["schema", "stats"]
    [shortcuts.cleansed]
    steps = ["filter:quality > 3", "hide:temp_col"]
  Then: pqr data.parquet --shortcut summary
""",
    )
    parser.add_argument(
        "file", nargs="?", help="Parquet file to open"
    )
    parser.add_argument(
        "file2", nargs="?", default=None, help="Second file for diff"
    )
    parser.add_argument(
        "--step", "-s", action="append", default=[],
        help="Step to run (repeatable). Format: name or name:key=value or name:expression"
    )
    parser.add_argument(
        "--steps", default=None,
        help="Comma-separated list of steps (alternative to --step)"
    )
    parser.add_argument(
        "--batch", action="store_true",
        help="Run steps without TUI (default when --step is given)"
    )
    parser.add_argument(
        "--tui", action="store_true",
        help="Open TUI after running steps"
    )
    parser.add_argument(
        "--sql", default=None,
        help="Shorthand for --step sql:EXPR"
    )
    parser.add_argument(
        "--filter", default=None,
        help="Shorthand for --step filter:EXPR"
    )
    parser.add_argument(
        "--sort", default=None,
        help="Shorthand for --step sort:column=NAME"
    )
    parser.add_argument(
        "--column", default=None,
        help="Column name for yank/sort/hide/stats (used with --step)"
    )
    parser.add_argument(
        "--row", default=None,
        help="Row index for yank/delete-row"
    )
    parser.add_argument(
        "--shortcut", default=None,
        help="Use a named shortcut from ~/.config/pqr/shortcuts.toml"
    )
    parser.add_argument(
        "--export", action="store_true", default=None,
        help="Shorthand for --step export (appended automatically)"
    )
    parser.add_argument(
        "--schema", action="store_true", default=None,
        help="Shorthand for --step schema (appended automatically)"
    )
    parser.add_argument(
        "--yank", default=None,
        help="Shorthand for --step yank:column=NAME"
    )
    parser.add_argument(
        "--output", default="-",
        help="Output file for export (default: stdout)"
    )
    parser.add_argument(
        "--format", choices=["csv", "json", "parquet"], default="csv",
        help="Export format (default: csv)"
    )
    return parser


def _load_shortcuts() -> dict:
    """Load custom shortcuts from ~/.config/pqr/shortcuts.toml."""
    import os
    import re
    shortcut_path = Path.home() / ".config" / "pqr" / "shortcuts.toml"
    if not shortcut_path.exists():
        return {}
    shortcuts = {}
    current = None
    for line in shortcut_path.read_text().splitlines():
        line = line.strip()
        m = re.match(r'^\[(\w+)\.(.+)\]$', line)
        if m:
            if m.group(1) == "shortcuts":
                current = m.group(2)
                shortcuts[current] = {}
            continue
        if current and line.startswith("steps = ["):
            items = re.findall(r'"([^"]*)"', line)
            shortcuts[current]["steps"] = items
        if current and line.startswith("description = "):
            desc = line.split("=", 1)[1].strip().strip('"')
            shortcuts[current]["description"] = desc
    return shortcuts


def main(argv):
    parser = _build_parser()
    args = parser.parse_args(argv[1:])

    if not args.file:
        parser.print_help()
        sys.exit(0)

    path = args.file
    if not Path(path).exists():
        print(f"Error: not found — {path}", file=sys.stderr)
        sys.exit(1)

    # Build step list from CLI args
    pipeline = Pipeline()

    # --steps (comma-separated)
    if args.steps:
        for spec in args.steps.split(","):
            spec = spec.strip()
            if spec:
                pipeline.add_spec(spec)

    # --step (repeated)
    for spec in args.step:
        pipeline.add_spec(spec)

    # --sql shorthand
    if args.sql:
        pipeline.add_spec(f"sql:{args.sql}")

    # --filter shorthand
    if args.filter:
        pipeline.add_spec(f"filter:{args.filter}")

    # --sort shorthand
    if args.sort:
        pipeline.add_spec(f"sort:column={args.sort}")

    # --yank shorthand
    if args.yank:
        row_arg = f";row={args.row}" if args.row else ""
        pipeline.add_spec(f"yank:column={args.yank}{row_arg}")

    # --schema shorthand
    if args.schema:
        pipeline.add_spec("schema")

    # --export shorthand (always last)
    if args.export:
        pipeline.add_spec(f"export:format={args.format};output={args.output}")

    # --shortcut
    if args.shortcut:
        shortcuts = _load_shortcuts()
        if args.shortcut not in shortcuts:
            print(f"Error: shortcut '{args.shortcut}' not found", file=sys.stderr)
            print(f"Available shortcuts: {', '.join(shortcuts.keys()) if shortcuts else 'none'}", file=sys.stderr)
            sys.exit(1)
        sc = shortcuts[args.shortcut]
        for spec in sc.get("steps", []):
            pipeline.add_spec(spec)

    # Decide mode: batch vs TUI
    batch_mode = args.batch or (bool(pipeline.steps) and not args.tui)

    if batch_mode and pipeline.steps:
        # Batch mode: run steps without TUI
        state = _build_state(path)
        results = pipeline.run(state)

        # Print results
        for result in results:
            if result.message:
                print(result.message)

        # If export was the last step, print the output
        last = results[-1] if results else None
        if last and last.output is not None:
            out_path = pipeline.steps[-1].args.get("output", "-") if pipeline.steps else "-"
            if out_path == "-":
                print(last.output)
            else:
                Path(out_path).write_text(last.output)

        # If last result has df and no export, print as CSV
        if last and last.df is not None and not args.export and last.output is None:
            print(last.df.to_csv(index=False))

    else:
        # TUI mode: run steps first, then open TUI with result
        if pipeline.steps:
            state = _build_state(path)
            results = pipeline.run(state)
            app = ParquetReader(str(state.path), args.file2)
            app._startup_df = state.df
            # Pass zst reader via schema for zst files, or pa.Schema for parquet
            app._startup_schema = state.args.get("_zst_reader") if _is_zst_file(path) else state.schema
            app.run()
        else:
            ParquetReader(path, args.file2).run()


if __name__ == "__main__":
    main(sys.argv)
