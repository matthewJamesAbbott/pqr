#!/usr/bin/env python3
"""pqr - Parquet Reader: a vim-like terminal viewer and editor for .parquet files."""

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.events import MouseScrollUp, MouseScrollDown, MouseScrollLeft, MouseScrollRight
from textual.widgets import (
    DataTable, Footer, Input, Label, RichLog, Static,
    DirectoryTree,
)
from textual.containers import Container, ScrollableContainer
from textual.message import Message
from rich.markup import escape
from rich.text import Text

from pqr.io.reader import JsonlReader, ParquetReader, ReaderFactory, Reader
from pqr.pipeline.state import PipelineState, StepResult
from pqr.pipeline.engine import Pipeline, Step

# ---------------------------------------------------------------------------
# Helper utilities (moved from monolith)
# ---------------------------------------------------------------------------

def _is_zst_file(path: str) -> bool:
    return path.endswith(".zst") or path.endswith(".zst.")


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


def _try_copy(text: str) -> bool:
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


try:
    import zstandard as zstd
except ImportError:
    zstd = None


# ---------------------------------------------------------------------------
# ViewCellScreen
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
# EditScreen
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
        parent: "ParquetReaderApp",
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
        yield Label(f" [{self._col_key}]  row {self._row_idx + 1}", id="ed-label")
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
# SchemaScreen
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
# FileBrowserScreen
# ---------------------------------------------------------------------------
class FileBrowserScreen(Screen[Optional[str]]):
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
# RecentFilesScreen
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
# DirBrowserScreen
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
        parquet_files = sorted(str(p) for p in Path(self._directory).glob("**/*.parquet"))
        zst_files = sorted(str(p) for p in Path(self._directory).glob("**/*.zst"))
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
                        reader = JsonlReader(fpath)
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
# SearchPrompt
# ---------------------------------------------------------------------------
class SearchPrompt(Container):
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
# ExportScreen
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
# FilterBar
# ---------------------------------------------------------------------------
class FilterBar(Input):
    class FilterChanged(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value
    def _on_input_changed(self) -> None:
        self.post_message(FilterBar.FilterChanged(self.value))


# ---------------------------------------------------------------------------
# PlainDataTable, LazyDataTable
# ---------------------------------------------------------------------------
class PlainDataTable(DataTable):
    def render_cell(self, value: object) -> Text:
        return Text(str(value), no_wrap=True)


class LazyDataTable(PlainDataTable):
    def __init__(self, parent: "ParquetReaderApp", *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._app_ref = parent
    def action_cursor_down(self) -> None:
        self._app_ref.action_down()
    def action_cursor_up(self) -> None:
        self._app_ref.action_up()
    def action_cursor_left(self) -> None:
        self._app_ref.action_left()
    def action_cursor_right(self) -> None:
        self._app_ref.action_right()
    def action_page_down(self) -> None:
        self._app_ref.action_page_down()
    def action_page_up(self) -> None:
        self._app_ref.action_page_up()
    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.prevent_default()
        event.stop()
        self._app_ref.action_down()
    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.prevent_default()
        event.stop()
        self._app_ref.action_up()
    def _on_mouse_scroll_right(self, event: MouseScrollRight) -> None:
        event.prevent_default()
        event.stop()
        self._app_ref.action_right()
    def _on_mouse_scroll_left(self, event: MouseScrollLeft) -> None:
        event.prevent_default()
        event.stop()
        self._app_ref.action_left()


# ---------------------------------------------------------------------------
# JumpToRowScreen
# ---------------------------------------------------------------------------
class JumpToRowScreen(Screen[Optional[int]]):
    CSS = """
        Screen {
            align: center middle;
            background: black 60%;
        }
        #jr-container {
            width: 40%;
            height: auto;
        }
        #jr-label {
            color: $accent;
        }
        #jr-input {
            width: 100%;
        }
    """
    BINDINGS = [
        Binding("enter,ctrl+j", "confirm", "Go"),
        Binding("escape", "cancel", "Cancel"),
    ]
    def __init__(self, total_rows: int) -> None:
        super().__init__()
        self._total_rows = total_rows
    def compose(self) -> ComposeResult:
        yield Label(f" Go to row (1-{self._total_rows}):", id="jr-label")
        yield Input(id="jr-input", placeholder="row number")
    def on_mount(self) -> None:
        self.query_one("#jr-input", Input).focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        event.prevent_default()
        try:
            row = int(val)
            if 1 <= row <= self._total_rows:
                self.dismiss(row - 1)
            else:
                self.dismiss(None)
        except ValueError:
            self.dismiss(None)
    def action_confirm(self) -> None:
        val = self.query_one("#jr-input", Input).value.strip()
        try:
            row = int(val)
            if 1 <= row <= self._total_rows:
                self.dismiss(row - 1)
            else:
                self.dismiss(None)
        except ValueError:
            self.dismiss(None)
    def action_cancel(self) -> None:
        self.dismiss(None)



# ---------------------------------------------------------------------------
# SearchScreen
# ---------------------------------------------------------------------------
class SearchScreen(Screen[Optional[dict]]):
    CSS = """
        Screen {
            align: center middle;
            background: black 60%;
        }
        #sr-container {
            width: 50%;
            height: auto;
            background: $surface;
            border: solid $accent;
            padding: 1 2;
        }
        #sr-title {
            color: $accent;
            width: 100%;
        }
        #sr-info {
            width: 100%;
            color: $text;
        }
        .sr-field {
            width: 100%;
        }
        .sr-label {
            width: 12;
            color: $text;
        }
        .sr-input {
            width: 1fr;
        }
        #sr-hint {
            dock: bottom;
            height: 1;
            color: $text;
            opacity: 70%;
        }
    """
    BINDINGS = [
        Binding("enter,ctrl+j", "search", "Search"),
        Binding("escape", "cancel", "Cancel"),
    ]
    def __init__(self, total_rows: int) -> None:
        super().__init__()
        self._total_rows = total_rows
    def compose(self) -> ComposeResult:
        yield Container(
            Label(" Search", id="sr-title"),
            Label(f" Total: {self._total_rows} rows", id="sr-info"),
            Container(
                Static("Pattern: ", classes="sr-label"),
                Input(id="sr-pattern", placeholder="search text", classes="sr-field"),
            ),
            Container(
                Static("Start: ", classes="sr-label"),
                Input(id="sr-start", placeholder="1", classes="sr-field"),
            ),
            Container(
                Static("End: ", classes="sr-label"),
                Input(id="sr-end", placeholder=str(self._total_rows), classes="sr-field"),
            ),
            id="sr-container",
        )
        yield Label("Enter to search  Esc to cancel", id="sr-hint")
    def on_mount(self) -> None:
        end_input = self.query_one("#sr-end", Input)
        end_input.value = str(self._total_rows)
        self.query_one("#sr-pattern", Input).focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.prevent_default()
        self.action_search()
    def action_search(self) -> None:
        pattern = self.query_one("#sr-pattern", Input).value.strip()
        if not pattern:
            return
        start_val = self.query_one("#sr-start", Input).value.strip()
        end_val = self.query_one("#sr-end", Input).value.strip()
        try:
            start = int(start_val) - 1 if start_val else 0
        except ValueError:
            start = 0
        try:
            end = int(end_val) if end_val else self._total_rows
        except ValueError:
            end = self._total_rows
        start = max(0, min(start, self._total_rows - 1))
        end = max(start + 1, min(end, self._total_rows))
        self.dismiss({"pattern": pattern, "start": start, "end": end})
    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# DiffScreen
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
# ParquetReaderApp — the main Textual application
# ---------------------------------------------------------------------------
class ParquetReaderApp(App[None]):
    BINDINGS = [
        Binding("j,down", "down", "Down"),
        Binding("k,up", "up", "Up"),
        Binding("h,left", "left", "Left"),
        Binding("l,right", "right", "Right"),
        Binding("g", "home", "Top"),
        Binding("ctrl+g", "jump_row", "GoRow"),
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
        #footer-shelf {
            dock: bottom;
            height: 2;
            background: $boost;
            color: $text;
            padding: 0 1;
            overflow: hidden hidden;
            content-align: left middle;
            text-overflow: ellipsis;
        }
    """

    @staticmethod
    def _calc_visible_rows() -> int:
        try:
            import os
            rows = os.get_terminal_size().lines
        except Exception:
            rows = 24
        return max(rows - 3, 10)

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
        self._parquet_reader: ParquetReader | None = None
        self._zst_reader: JsonlReader | None = None
        self._num_rows: int = 0
        self._lazy: bool = False
        self._visible_rows: int = self._calc_visible_rows()
        self._view_offset: int = 0
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
            self.push_screen(DiffScreen(str(self._path), str(self._path2)))
            return
        if self._path is None:
            self.push_screen(RecentFilesScreen(), callback=self._on_file_chosen)
            return
        if self._startup_df is not None:
            self._df = self._startup_df
            self._schema = self._startup_schema
            self._num_rows = len(self._df)
            if _is_zst_file(str(self._path)):
                self._zst_reader = self._startup_schema if isinstance(self._startup_schema, JsonlReader) else None
            else:
                self._parquet_reader = ParquetReader(str(self._path))
            await self._populate_table(self._df)
            self._update_status()
            self._startup_df = None
            self._startup_schema = None
            return
        target = self._path
        if target.is_dir():
            self.push_screen(DirBrowserScreen(str(target)), callback=self._on_file_chosen)
            return
        if _is_zst_file(str(target)):
            await self._open_zst(str(target))
        else:
            await self._open_parquet(str(target))

    async def _on_file_chosen(self, path: str | None) -> None:
        if path:
            self._path = Path(path)
            if self._path.is_dir():
                self.push_screen(DirBrowserScreen(str(self._path)), callback=self._on_file_chosen)
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
        self._parquet_reader = None
        if self._zst_reader is not None:
            self._zst_reader.close()
        self._zst_reader = None
        self._num_rows = 0
        self._lazy = False
        self._view_offset = 0
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
        self._parquet_reader = ParquetReader(path)
        self._schema = self._parquet_reader.columns  # schema names via reader
        self._schema = pq.ParquetFile(path).schema_arrow
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
        self._zst_reader = JsonlReader(path)
        self._zst_reader._ensure_indexed()
        self._num_rows = self._zst_reader.num_rows
        self._all_columns = self._zst_reader.columns
        self._lazy = True
        self._view_offset = 0
        self._df = self._zst_reader.get_row_range(0, min(self._visible_rows, self._num_rows))
        self._col_names = self._all_columns
        self._types = {c: self._zst_reader.dtypes.get(c, "object") for c in self._all_columns}
        dt = LazyDataTable(self, show_cursor=True, zebra_stripes=True, show_row_labels=True)
        self._col_keys = dt.add_columns(*self._all_columns)
        self._dt = dt
        self.mount(dt)
        self._mount_bars()
        self._render_zst_visible_rows(self._view_offset, min(self._view_offset + self._visible_rows, self._num_rows))
        self._update_row_labels()
        self._update_status()

    def _notify_zstd_missing(self) -> None:
        try:
            self.exit()
        except Exception:
            pass
        print("Error: zstandard module not installed. Install with: pip install zstandard", file=sys.stderr)
        sys.exit(1)

    def _load_lazy_initial(self) -> None:
        if self._parquet_reader is None:
            return
        self._view_offset = 0
        table = self._parquet_reader.get_row_range(0, min(self._visible_rows, self._num_rows))
        self._df = table
        col_names = list(self._df.columns)
        self._col_names = col_names
        self._types = {c: str(t) for c, t in self._df.dtypes.to_dict().items()}
        dt = LazyDataTable(self, show_cursor=True, zebra_stripes=True, show_row_labels=True)
        self._col_keys = dt.add_columns(*col_names)
        self._dt = dt
        self.mount(dt)
        self._mount_bars()
        self._render_visible_rows(self._view_offset, min(self._view_offset + self._visible_rows, self._num_rows))
        self._update_row_labels()

    async def _render_zst_visible_rows_async(self, start: int, end: int) -> None:
        if self._zst_reader is None:
            return
        dt = self._dt
        if dt is None:
            return
        df_chunk = await self._zst_reader.get_row_range_async(start, end)
        dt.clear(columns=True)
        self._col_keys = dt.add_columns("#", *self._all_columns)
        rows_data = [
            [str(idx + 1)] + [self._fmt(v) for v in df_chunk.iloc[idx - start].values]
            for idx in range(start, min(end, self._num_rows))
        ]
        self._row_keys = list(dt.add_rows(rows_data))
        for ri in range(start, min(end, self._num_rows)):
            for ci, v in enumerate(df_chunk.iloc[ri - start].values):
                self._raw[(ri, ci)] = self._full(v)

    def _render_zst_visible_rows(self, start: int, end: int) -> None:
        if self._zst_reader is None:
            return
        dt = self._dt
        if dt is None:
            return
        df_chunk = self._zst_reader.get_row_range(start, end)
        dt.clear(columns=True)
        self._col_keys = dt.add_columns("#", *self._all_columns)
        rows_data = [
            [str(idx + 1)] + [self._fmt(v) for v in df_chunk.iloc[idx - start].values]
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
        shelf = Static("", id="footer-shelf")
        self.mount(shelf)
        self._footer_shelf = shelf
        self._update_footer_shelf()

    def _render_visible_rows(self, start: int, end: int) -> None:
        if self._zst_reader is not None:
            self._render_zst_visible_rows(start, end)
            return
        if self._lazy and self._parquet_reader is None:
            return
        dt = self._dt
        if dt is None:
            return
        if self._lazy:
            df_chunk = self._get_row_range(start, end)
            dt.clear(columns=True)
            self._col_keys = dt.add_columns("#", *self._col_names)
            rows_data = [
                [str(idx + 1)] + [self._fmt(v) for v in df_chunk.iloc[idx - start].values]
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
                [str(idx + 1)] + [self._fmt(v) for v in self._df.iloc[idx].values]
                for idx in range(start, min(end, len(self._df)))
            ]
            dt.clear(columns=True)
            self._col_keys = dt.add_columns("#", *self._col_names)
            self._row_keys = list(dt.add_rows(rows_data))

    def _get_row_range(self, start: int, end: int) -> pd.DataFrame:
        if self._zst_reader is not None:
            return self._zst_reader.get_row_range(start, end)
        if self._parquet_reader is None:
            return pd.DataFrame()
        return self._parquet_reader.get_row_range(start, end)

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
            self._col_keys = dt.add_columns("#", *col_names)
        else:
            dt = self._dt
            for ckey in list(self._col_keys):
                try:
                    dt.remove_column(ckey)
                except Exception:
                    pass
            self._col_keys = dt.add_columns("#", *col_names)
        self._row_keys = list(
            dt.add_rows([
                [str(idx + 1)] + [self._fmt(v) for v in df.iloc[idx].values]
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
        if ParquetReaderApp._is_container(v):
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
        if self._lazy and row is not None:
            file_row = self._view_offset + row + 1
            parts.append(f"row {file_row}/{nr}")
        elif row is not None:
            parts.append(f"[{row+1}/{nr}]")
        if row is not None and col is not None and col > 0 and col <= nc:
            col_name = self._col_names[col - 1]
            parts.append(col_name)
            if self._filter_active:
                parts.append("FILTERED")
            parts.append(f"{len(self._edited)} edit(s)")
            if self._df is not None and col - 1 < len(self._df.columns):
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

    def on_resize(self, event) -> None:
        new_rows = self._calc_visible_rows()
        if self._lazy and self._dt is not None and new_rows != self._visible_rows:
            self._visible_rows = new_rows
            if self._view_offset + new_rows > self._num_rows:
                self._view_offset = max(0, self._num_rows - new_rows)
            self._refetch_visible()
        else:
            self._visible_rows = new_rows

    def action_down(self) -> None:
        if self._lazy and self._dt is not None:
            row = self._dt.cursor_row or 0
            if row >= self._dt.row_count - 1 and self._view_offset + self._dt.row_count < self._num_rows:
                self._view_offset += self._visible_rows
                self._refetch_visible()
                return
        self._dt.move_cursor(row=(self._dt.cursor_row or 0) + 1)
        self._on_cursor_moved()

    def action_up(self) -> None:
        if self._lazy and self._dt is not None:
            row = self._dt.cursor_row or 0
            if row == 0 and self._view_offset > 0:
                self._view_offset = max(0, self._view_offset - self._visible_rows)
                self._refetch_visible()
                self._dt.move_cursor(row=self._dt.row_count - 1)
                return
        self._dt.move_cursor(row=(self._dt.cursor_row or 0) - 1)
        self._on_cursor_moved()

    def action_left(self) -> None:
        col = self._dt.cursor_column or 0
        if col > 0:
            self._dt.move_cursor(column=col - 1)
            self._on_cursor_moved()

    def action_right(self) -> None:
        col = self._dt.cursor_column or 0
        max_col = len(self._col_names) if self._col_names else 1
        if col < max_col:
            self._dt.move_cursor(column=col + 1)
            self._on_cursor_moved()

    def action_home(self) -> None:
        if self._lazy:
            self._view_offset = 0
            self._refetch_visible()
        if self._dt.row_count:
            self._dt.cursor_coordinate = (0, 0)
        self._on_cursor_moved()

    def action_end(self) -> None:
        if self._lazy:
            nr = self._num_rows
            self._view_offset = max(0, nr - self._visible_rows)
            self._refetch_visible()
        col = self._dt.cursor_column or 0
        nr = self._num_rows if self._lazy else (len(self._df) if self._df is not None else 0)
        if nr:
            self._dt.cursor_coordinate = (min(self._dt.row_count - 1, nr - 1), col)
        self._on_cursor_moved()

    def action_page_down(self) -> None:
        if self._lazy:
            self._view_offset += self._visible_rows
            if self._view_offset + self._visible_rows > self._num_rows:
                self._view_offset = max(0, self._num_rows - self._visible_rows)
            self._refetch_visible()
        else:
            self._dt.scroll_page_down()
        self._on_cursor_moved()

    def action_page_up(self) -> None:
        if self._lazy:
            self._view_offset = max(0, self._view_offset - self._visible_rows)
            self._refetch_visible()
        else:
            self._dt.scroll_page_up()
        self._on_cursor_moved()

    def _on_cursor_moved(self) -> None:
        self._update_status()

    def _refetch_visible(self) -> None:
        if not self._lazy or self._dt is None:
            return
        start = self._view_offset
        end = start + self._visible_rows
        if self._zst_reader is not None:
            self._render_zst_visible_rows(start, end)
        else:
            self._render_visible_rows(start, end)
        self._update_row_labels()

    def _update_row_labels(self) -> None:
        if self._dt is None or not self._lazy:
            return
        for ri in range(self._dt.row_count):
            if ri >= len(self._row_keys):
                break
            rkey = self._row_keys[ri]
            label = self._view_offset + ri + 1
            try:
                self._dt.labeled_row(rkey, str(label))
            except Exception:
                pass
        self._dt.refresh()

    def _update_footer_shelf(self) -> None:
        if hasattr(self, '_footer_shelf') and self._footer_shelf is not None:
            shelf = self._footer_shelf
            items = []
            for binding in self.BINDINGS:
                if binding.show:
                    key_display = binding.key_display if binding.key_display else binding.key
                    items.append(f"[cyan]{key_display}[/cyan] {binding.description}")
            shelf.update(" ".join(items))

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
        col_name = self._col_names[col - 1] if col > 0 and col - 1 < len(self._col_names) else "?"
        return row, col - 1, display, str(row), col_name

    def _open_edit(self, append: bool = False) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        abs_row = self._view_offset + ri if self._lazy else ri
        key = (abs_row, ci)
        current = self._edited.get(key, self._raw.get(key, display))
        original = self._origins.get(key, self._raw.get(key, display))
        self.push_screen(
            EditScreen(self, abs_row, ci, current, original, str(abs_row), ck, append),
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
        abs_row = self._view_offset + row if self._lazy else row
        ci = col - 1 if col > 0 else 0
        text_str = self._raw.get((abs_row, ci), "")
        self.push_screen(ViewCellScreen(text_str))

    def edit_cell(self, row_idx: int, col_idx: int, row_key: str, col_key: str, new_value: str, original: str) -> None:
        key = (row_idx, col_idx)
        if key not in self._origins:
            self._origins[key] = original
        self._edited[key] = new_value
        dt = self._dt
        local_row = row_idx - self._view_offset if self._lazy else row_idx
        rkey = self._row_keys[local_row]
        ckey = self._col_keys[col_idx + 1]
        dt.update_cell(rkey, ckey, new_value)
        self._update_status()

    def _edit_callback(self, result: bool) -> None:
        self._update_status()

    def action_save(self) -> None:
        if not self._edited and not self._deleted_rows:
            self.notify("[green]No changes to save.[/green]")
            return
        if self._zst_reader is not None:
            df = self._zst_reader.get_row_range(0, self._num_rows)
        elif self._lazy:
            df = pq.read_table(str(self._path)).to_pandas() if self._parquet_reader is not None else None
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

    def action_export(self) -> None:
        self.push_screen(ExportScreen(), callback=self._do_export)

    def _do_export(self, fmt: str | None) -> None:
        if fmt is None:
            return
        if self._zst_reader is not None:
            df = self._zst_reader.get_row_range(0, self._num_rows)
        elif self._lazy and self._parquet_reader is not None:
            df = pq.read_table(str(self._path)).to_pandas()
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

    def action_open_file(self) -> None:
        initial = str(self._path.parent) if self._path else "."
        self.push_screen(FileBrowserScreen(initial), callback=lambda path: self._on_open_file(path))

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

    def _build_state(self) -> PipelineState:
        from pqr.io.reader import Reader
        return PipelineState(
            df=self._filter_df if self._filter_active else self._df,
            schema=self._schema if self._zst_reader is None else self._zst_reader,
            path=self._path,
            hidden_cols=self._hidden_cols,
            sort_col=self._sort_col,
            sort_asc=self._sort_asc,
            clipboard=self._clipboard,
            _reader=self._zst_reader if self._zst_reader else self._parquet_reader,
        )

    async def _run_step(self, step: Step) -> StepResult:
        from pqr.commands.impl.core_commands import FilterCmd, HideCmd, SearchCmd, SortCmd, StatsCmd
        from pqr.commands.impl.io_commands import ExportCmd, SchemaCmd
        from pqr.commands.impl.util_commands import DeleteCmd, PythonCmd, ShellCmd, SqlCmd, YankCmd
        _STEP_CMD_MAP = {
            "schema": SchemaCmd,
            "yank": YankCmd,
            "search": SearchCmd,
            "filter": FilterCmd,
            "sort": SortCmd,
            "hide": HideCmd,
            "sql": SqlCmd,
            "stats": StatsCmd,
            "delete-row": DeleteCmd, "delete_row": DeleteCmd, "delrow": DeleteCmd,
            "export": ExportCmd,
            "python": PythonCmd,
            "shell": ShellCmd,
        }
        handler = _STEP_CMD_MAP.get(step.name)
        if handler is None:
            self.notify(f"[red]Unknown step: {step.name}[/red]")
            return StepResult(message=f"Unknown step: {step.name}")
        state = self._build_state()
        state.args = dict(step.args)
        result = handler(state.args).execute(state)
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
                        if ci > 0 and self._col_names[ci - 1] == col:
                            self._dt.show_column(ck)
                            break
                except Exception:
                    pass
        return result

    async def _run_steps(self, specs: list[str]) -> None:
        for spec in specs:
            step = Step.parse_spec(spec)
            await self._run_step(step)

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

    def action_search(self) -> None:
        self.push_screen(SearchScreen(self._num_rows), callback=self._on_search_result)

    def _on_search_result(self, result: Optional[dict]) -> None:
        if result is None:
            self._dt.focus()
            return
        pattern = result["pattern"]
        start = result["start"]
        end = result["end"]
        self._search_pattern = pattern
        self._search_matches = []
        self._search_cursor = -1
        if self._lazy:
            self._do_search_lazy(pattern, start, end)
        else:
            self._do_search_full(pattern, start, end)

    def _do_search_lazy(self, pattern: str, start: int, end: int) -> None:
        if self._zst_reader is None:
            self.notify("[yellow]No data to search.[/yellow]")
            self._dt.focus()
            return
        pattern_lower = pattern.lower()
        chunk_size = 1000
        for row_start in range(start, end, chunk_size):
            row_end = min(row_start + chunk_size, end)
            df_chunk = self._zst_reader.get_row_range(row_start, row_end)
            for ri in range(len(df_chunk)):
                abs_row = row_start + ri
                for ci, col_name in enumerate(self._col_names):
                    if ci < len(df_chunk.columns):
                        val = df_chunk.iloc[ri, ci]
                        matched = False
                        if ParquetReaderApp._is_container(val):
                            if val.size > 0 and pattern_lower in str(val).lower():
                                matched = True
                        elif val is not None and not pd.isna(val):
                            if pattern_lower in str(val).lower():
                                matched = True
                        if matched:
                            self._search_matches.append((abs_row, ci))
        self._finalize_search(pattern, end - start)

    def _do_search_full(self, pattern: str, start: int, end: int) -> None:
        df = self._filter_df if self._filter_active else self._df
        if df is None:
            self.notify("[yellow]No data to search.[/yellow]")
            self._dt.focus()
            return
        pattern_lower = pattern.lower()
        end = min(end, len(df))
        for ri in range(end - start):
            abs_row = start + ri
            for ci, col_name in enumerate(self._col_names):
                if ci < len(df.columns):
                    val = df.iloc[abs_row, ci]
                    matched = False
                    if ParquetReaderApp._is_container(val):
                        if val.size > 0 and pattern_lower in str(val).lower():
                            matched = True
                    elif val is not None and not pd.isna(val):
                        if pattern_lower in str(val).lower():
                            matched = True
                    if matched:
                        self._search_matches.append((abs_row, ci))
        self._finalize_search(pattern, end - start)

    def _finalize_search(self, pattern: str, searched_range: int) -> None:
        if self._search_matches:
            self._search_cursor = 0
            r, c = self._search_matches[0]
            self._navigate_to_row(r)
            self._update_status()
            self._highlight_matches_in_view()
            self._show_search_bar(pattern, len(self._search_matches))
            self._dt.focus()
        else:
            self.notify(f"[yellow]No matches found in {searched_range} rows.[/yellow]")
            self._dt.focus()

    def _highlight_matches_in_view(self) -> None:
        if self._dt is None:
            return
        match_set = set(self._search_matches)
        for ri in range(self._dt.row_count):
            if ri >= len(self._row_keys):
                break
            abs_row = self._view_offset + ri
            for ci in range(len(self._col_keys)):
                rkey = self._row_keys[ri]
                ckey = self._col_keys[ci]
                if (abs_row, ci - 1) in match_set:
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

    def _navigate_to_row(self, row: int) -> None:
        if self._lazy:
            target_view = (row // self._visible_rows) * self._visible_rows
            if target_view != self._view_offset:
                self._view_offset = target_view
                self._refetch_visible()
            local_row = row - self._view_offset
            self._dt.move_cursor(row=min(local_row, self._dt.row_count - 1))
        else:
            if row < self._dt.row_count:
                self._dt.cursor_coordinate = (row, self._dt.cursor_column or 0)

    def action_search_next(self) -> None:
        if not self._search_matches:
            return
        self._search_cursor = (self._search_cursor + 1) % len(self._search_matches)
        r, c = self._search_matches[self._search_cursor]
        self._navigate_to_row(r)
        self._update_status()

    def action_search_prev(self) -> None:
        if not self._search_matches:
            return
        self._search_cursor = (self._search_cursor - 1) % len(self._search_matches)
        r, c = self._search_matches[self._search_cursor]
        self._navigate_to_row(r)
        self._update_status()

    def action_jump_row(self) -> None:
        self.push_screen(JumpToRowScreen(self._num_rows), callback=self._on_jump_row)

    async def _on_jump_row(self, row_idx: Optional[int]) -> None:
        if row_idx is not None:
            if self._lazy:
                target_view = (row_idx // self._visible_rows) * self._visible_rows
                if target_view != self._view_offset:
                    self._view_offset = target_view
                    await self._render_zst_visible_rows_async(
                        self._view_offset,
                        min(self._view_offset + self._visible_rows, self._num_rows)
                    )
                    self._update_row_labels()
                local_row = row_idx - self._view_offset
                self._dt.move_cursor(row=min(local_row, self._dt.row_count - 1))
            else:
                self._navigate_to_row(row_idx)
            self._update_status()
        self._dt.focus()

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

    def action_yank_cell(self) -> None:
        info = self._get_cell()
        if info is None:
            return
        ri, ci, display, rk, ck = info
        abs_row = self._view_offset + ri if self._lazy else ri
        self._clipboard = self._raw.get((abs_row, ci), display)
        if _try_copy(self._clipboard):
            self.notify(f"[green]Yanked:[/green] {self._clipboard[:80]}")
        else:
            self.notify("[red]Yank failed:[/red] no clipboard backend available")

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
                self.post_message(ParquetReaderApp.SqlPrompt.SqlSubmitted(inp.value))
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
                self._parquet_reader = ParquetReader(path)
                self._schema = self._parquet_reader.columns
                self._schema = pq.ParquetFile(path).schema_arrow
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

    def action_quit(self) -> None:
        if self._edited:
            self.notify("[yellow]Unsaved changes — press 'w' to save first.[/yellow]")
        else:
            self.exit()

    def on_exit(self, event: "Exit[None]") -> None:
        self.screen.styles.background = "transparent"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _build_parser():
    import argparse
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
    parser.add_argument("file", nargs="?", help="Parquet file to open")
    parser.add_argument("file2", nargs="?", default=None, help="Second file for diff")
    parser.add_argument("--step", "-s", action="append", default=[], help="Step to run (repeatable)")
    parser.add_argument("--steps", default=None, help="Comma-separated list of steps")
    parser.add_argument("--batch", action="store_true", help="Run steps without TUI")
    parser.add_argument("--tui", action="store_true", help="Open TUI after running steps")
    parser.add_argument("--sql", default=None, help="Shorthand for --step sql:EXPR")
    parser.add_argument("--filter", default=None, help="Shorthand for --step filter:EXPR")
    parser.add_argument("--sort", default=None, help="Shorthand for --step sort:column=NAME")
    parser.add_argument("--column", default=None, help="Column name for yank/sort/hide/stats")
    parser.add_argument("--row", default=None, help="Row index for yank/delete-row")
    parser.add_argument("--shortcut", default=None, help="Named shortcut from ~/.config/pqr/shortcuts.toml")
    parser.add_argument("--export", action="store_true", default=None, help="Shorthand for --step export")
    parser.add_argument("--schema", action="store_true", default=None, help="Shorthand for --step schema")
    parser.add_argument("--yank", default=None, help="Shorthand for --step yank:column=NAME")
    parser.add_argument("--output", default="-", help="Output file for export")
    parser.add_argument("--format", choices=["csv", "json", "parquet"], default="csv", help="Export format")
    return parser


def main(argv):
    from pqr.app.facade import PQRSTask, _load_shortcuts

    parser = _build_parser()
    args = parser.parse_args(argv[1:])

    if not args.file:
        parser.print_help()
        sys.exit(0)

    path = args.file
    if not Path(path).exists():
        print(f"Error: not found — {path}", file=sys.stderr)
        sys.exit(1)

    task = PQRSTask(path)
    pipeline = PQRSTask.build_pipeline_from_args(args)

    batch_mode = args.batch or (bool(pipeline.steps) and not args.tui)

    if batch_mode and pipeline.steps:
        state = task.build_state(path)
        results = pipeline.run(state)
        for result in results:
            if result.message:
                print(result.message)
        last = results[-1] if results else None
        if last and last.output is not None:
            out_path = pipeline.steps[-1].args.get("output", "-") if pipeline.steps else "-"
            if out_path == "-":
                print(last.output)
            else:
                Path(out_path).write_text(last.output)
        if last and last.df is not None and not args.export and last.output is None:
            print(last.df.to_csv(index=False))
    else:
        if pipeline.steps:
            state = task.build_state(path)
            results = pipeline.run(state)
            app = ParquetReaderApp(str(state.path), args.file2)
            app._startup_df = state.df
            app._startup_schema = state._reader if _is_zst_file(path) else state.schema
            try:
                app.run()
            finally:
                import shutil
                rows, cols = shutil.get_terminal_size((24, 80))
                print("\033[H\033[J", end="")
                for _ in range(rows):
                    print()
        else:
            try:
                ParquetReaderApp(path, args.file2).run()
            finally:
                import shutil
                rows, cols = shutil.get_terminal_size((24, 80))
                print("\033[H\033[J", end="")
                for _ in range(rows):
                    print()


if __name__ == "__main__":
    main(sys.argv)


def cli():
    """Console script entry point (no arguments needed)."""
    main(sys.argv)
