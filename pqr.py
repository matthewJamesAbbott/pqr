import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Label


class EditScreen(Screen[bool]):
    """Overlay for editing a single cell."""

    CSS = """
        Screen {
            align: center middle;
            background: black 60%;
        }
        #ed-container {
            width: 60%;
            height: auto;
            layout: vertical;
            gap: 1;
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
    ) -> None:
        super().__init__()
        self._parent = parent
        self._row_idx = row_idx
        self._col_idx = col_idx
        self._value = value
        self._original = original
        self._row_key = row_key
        self._col_key = col_key

    def compose(self):
        yield Label(f" [{self._col_key}]  row {self._row_idx}", id="ed-label")
        yield Input(id="ed-input", value=self._value)

    def on_mount(self) -> None:
        self.query_one("#ed-input", Input).focus()

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


class ParquetReader(App[None]):
    """Main application for viewing and editing parquet files."""

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
        Binding("w", "save", "Save"),
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
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = Path(path)
        self._df: pd.DataFrame | None = None
        self._dt: DataTable | None = None
        self._types: dict[str, str] = {}
        self._col_names: list[str] = []
        self._edited: dict[tuple[int, int], str] = {}
        self._origins: dict[tuple[int, int], str] = {}

    # -- mount ---------------------------------------------------------------
    def on_mount(self) -> None:
        self._df = pq.read_table(str(self._path)).to_pandas()
        col_names = list(self._df.columns)
        self._col_names = col_names
        self._types = {c: str(t) for c, t in self._df.dtypes.to_dict().items()}

        dt = DataTable(
            show_cursor=True,
            zebra_stripes=True,
        )

        for col in col_names:
            dt.add_column(col)

        for idx in range(len(self._df)):
            row = self._df.iloc[idx]
            dt.add_row(*[self._fmt(v) for v in row.values])

        self._dt = dt
        self.mount(dt)
        self.mount(Label(id="status-bar"))
        self.mount(Footer())

        self._update_status()

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _fmt(v) -> str:
        if pd.isna(v):
            return ""
        return str(v)

    def _update_status(self) -> None:
        dt = self._dt
        if dt is None:
            return
        nr = dt.row_count
        nc = len(self._col_names)
        row = dt.cursor_row
        col = dt.cursor_column
        if row is None:
            t = f" {self._path.name} |  {nr} rows | {nc} cols"
        else:
            col_name = self._col_names[col] if col < nc else "?"
            t = f" {self._path.name}  [{row+1}/{nr}]  {col_name}  |{len(self._edited)} edit(s)  j/k h/l g/G i w q"
        self.query_one("#status-bar", Label).update(t)

    # -- navigation ----------------------------------------------------------
    def action_down(self) -> None:
        self._dt.cursor_down()
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_up(self) -> None:
        self._dt.cursor_up()
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_left(self) -> None:
        self._dt.cursor_left()
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_right(self) -> None:
        self._dt.cursor_right()
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_home(self) -> None:
        if self._dt.row_count:
            self._dt.cursor_coordinate = (0, 0)
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_end(self) -> None:
        nr = self._dt.row_count
        nc = len(self._col_names)
        if nr and nc:
            self._dt.cursor_coordinate = (nr - 1, nc - 1)
        self._dt.ensure_visible(self._dt.cursor_coordinate)
        self._update_status()

    def action_page_down(self) -> None:
        self._dt.scroll_page_down()
        self._update_status()

    def action_page_up(self) -> None:
        self._dt.scroll_page_up()
        self._update_status()

    # -- editing -------------------------------------------------------------
    def _get_cell(self) -> tuple[int, int, str, str, str] | None:
        """Return (row_idx, col_idx, display_value, row_key_str, col_name)."""
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
        original = self._origins.get(key, display)
        self.push_screen(
            EditScreen(self, ri, ci, display, original, rk, ck),
            callback=self._edit_callback,
        )
        if append:
            inp = self.screen.query_one("#ed-input", Input)
            inp.cursor_position = len(inp.value)

    def action_edit(self) -> None:
        self._open_edit(append=False)

    def action_edit_append(self) -> None:
        self._open_edit(append=True)

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
        dt.update_cell((row_idx, col_idx), new_value)
        self._update_status()

    def _edit_callback(self, result: bool) -> None:
        if not result:
            info = self._get_cell()
            if info:
                ri, ci, display, _, _ = info
                key = (ri, ci)
                if key in self._edited:
                    if self._edited[key] == self._origins.get(key, ""):
                        del self._edited[key]
        self._update_status()

    # -- save ----------------------------------------------------------------
    def action_save(self) -> None:
        if not self._edited:
            self.notify("[green]No changes to save.[/green]")
            return

        df = self._df.copy()
        for (ri, ci), nv in self._edited.items():
            ci_actual = min(ci, len(self._col_names) - 1)
            ck = self._col_names[ci_actual]
            try:
                cv = self._convert(nv, self._types.get(ck, "object"))
                df.iloc[ri, ci_actual] = cv
            except (ValueError, TypeError, IndexError):
                pass

        out = self._path.with_stem(self._path.stem + "_edited")
        pq.write_table(pa.Table.from_pandas(df), str(out))
        self.notify(f"[green]Saved to {out.name}[/green]")
        self._edited.clear()
        self._origins.clear()
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

    # -- quit ----------------------------------------------------------------
    def action_quit(self) -> None:
        if self._edited:
            self.notify("[yellow]Unsaved changes — press 'w' to save first.[/yellow]")
        else:
            self.exit()


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("pqr — Parquet viewer & editor\n\nUsage: pqr <file.parquet>\n\nNavigate:  j/k  h/l  g/G  arrow keys  PgUp/PgDn\nEdit:      i  e  (edit cell)  a  (append)\nActions:   w  (save)  q  (quit)")
        sys.exit(0)
    path = argv[1]
    if not Path(path).exists():
        print(f"Error: not found — {path}", file=sys.stderr)
        sys.exit(1)
    ParquetReader(path).run()


if __name__ == "__main__":
    main(sys.argv)
