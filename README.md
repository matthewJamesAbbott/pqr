# pqr — Parquet Viewer & Editor for Power Users

A fast, **Vim-like** terminal UI for browsing, searching, editing, and wrangling large collections of Parquet files.

Built for people who actually live in their data — especially RAG pipelines, massive datasets, and local knowledge bases.

**Unique superpowers:**
- Browse entire directories of Parquets like a file manager
- Global `/` search across your whole collection → `y` yank to clipboard
- In-place cell editing with type safety
- One-page-per-row book RAG workflows (DDC trees, summaries, metadata)

![PQR 1][pqr1.png]

![PQR 2][pqr2.png]

![PQR 3][pqr3.png]

---

## Features

- **Terminal UI:** Modern, responsive TUI with zebra-striped tables and a dynamic status bar.
- **Vim-like Navigation:** `j`/`k` for up/down, `h`/`l` for left/right, `g`/`G` for top/bottom, plus `PgUp`/`PgDn`.
- **In-Place Editing:** Edit cells with `i` or `e`, append to values with `a`.
- **Full Cell Viewer:** Press `v` to inspect truncated cell contents in a scrollable popup.
- **Yank Cell:** Press `y` to copy cell value to clipboard.
- **Add Row:** Press `O` to append a new empty row.
- **Delete Row:** Press `dd` to mark a row for deletion; applied on save (`w`).
- **Sort Column:** Press `s` on a column to sort ascending/descending (toggle on repeat).
- **Hide/Show Column:** Press `H` while cursor is on a column to toggle visibility.
- **Type-Aware Saving:** Automatically converts edited strings back to original Parquet types (`int`, `float`, `bool`, `datetime`, `string`).
- **Safe Workflows:** Tracks edits, warns on unsaved changes, and exports to a new `<filename>_edited.parquet` file.
- **Export:** Press `W` to export as CSV, Excel (`.xlsx`), or Parquet.
- **Global Search:** Press `/` to search across all columns (vim-style bottom prompt); `n`/`N` to jump between matches.
- **Column Filtering:** Press `f` to toggle a filter bar; type `column == value` to filter rows.
- **SQL Query Mode:** Press `:` to run a DuckDB query against the loaded data (requires `pip install duckdb`).
- **Column Statistics:** Press `x` to show descriptive stats (mean, std, min, max, quartiles, nulls, unique counts) for the current column.
- **Live Stats Bar:** Status bar shows min, max, mean, and null count for numeric columns as you navigate.
- **Schema Viewer:** Press `S` to inspect the Parquet schema (types, encodings, null counts, compressed sizes).
- **Multi-File Tabs:** Open several files with `o`; switch between them with `Tab`/`Shift+Tab` or `gt`/`gT`.
- **File Browser:** Press `o` or `Ctrl+o` to open a file picker and switch files without exiting.
- **Recent Files:** Last 10 opened files persisted in `~/.pqr_history`; shown on startup when run with no arguments.
- **Directory Browsing:** Run `pqr ./data_folder/` to browse all `.parquet` files in a directory with metadata (rows, columns, size).
- **Diff Mode:** Run `pqr file_v1.parquet file_v2.parquet` to compare two files side-by-side.
- **Lazy Loading:** Files over 5,000 rows use row-group-based virtual scrolling for efficient memory usage.

---

## Installation

Requires Python 3.9+ and the following dependencies:

```bash
pip install pandas pyarrow textual
```

For Excel export support:

```bash
pip install openpyxl
```

For SQL query mode:

```bash
pip install duckdb
```

---

## Usage

**Open a single file:**

```bash
python pqr path/to/your/data.parquet
```

**Browse a directory of parquet files:**

```bash
python pqr path/to/data_folder/
```

**Compare two files side-by-side:**

```bash
python pqr file_v1.parquet file_v2.parquet
```

**Start without arguments (shows recent files):**

```bash
python pqr
```

---

## Keyboard Shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `j` / `↓` | Move down |
| `k` / `↑` | Move up |
| `h` / `←` | Move left |
| `l` / `→` | Move right |
| `g` | Jump to top |
| `G` | Jump to bottom |
| `Ctrl+F` / `PgDn` | Page down |
| `Ctrl+B` / `PgUp` | Page up |

### Editing

| Key | Action |
|-----|--------|
| `i` / `e` | Edit cell value |
| `a` | Append to cell value |
| `v` | View full cell contents |
| `y` | Yank (copy) cell to clipboard |
| `O` | Add new empty row |
| `dd` | Mark row for deletion |
| `Enter` / `Ctrl+J` | Confirm edit |
| `Esc` | Cancel edit |

### Search & Filter

| Key | Action |
|-----|--------|
| `/` | Search across all columns (vim-style prompt) |
| `n` | Next search match |
| `N` | Previous search match |
| `f` | Toggle column filter bar |
| `:` | SQL query mode (DuckDB) |

### Data Manipulation

| Key | Action |
|-----|--------|
| `s` | Sort current column (toggle ascending/descending) |
| `H` | Hide/show current column |
| `x` | Show column statistics |

### File & Export

| Key | Action |
|-----|--------|
| `w` | Save to `<filename>_edited.parquet` |
| `W` | Export (CSV / Excel / Parquet) |
| `o` / `Ctrl+o` | Open file browser |
| `S` | View Parquet schema |

### Tabs

| Key | Action |
|-----|--------|
| `Tab` / `gt` | Next tab |
| `Shift+Tab` / `gT` | Previous tab |

### Other

| Key | Action |
|-----|--------|
| `q` | Quit (warns if unsaved edits exist) |

---

## How It Works

1. Loads the Parquet file via `pyarrow` into a `pandas.DataFrame` (or lazily via row groups for large files).
2. Renders the data in a `textual` `DataTable` with cursor tracking and zebra-striped rows.
3. Captures cell edits in an overlay screen, preserving original values for type-aware conversion.
4. On save (`w`), applies type-aware conversions, drops marked-deletion rows, and writes a new Parquet file with an `_edited` suffix.
5. Maintains an edit counter in the status bar with live column statistics for numeric types.
6. Supports filtering, full-text search, column sorting, SQL queries, schema inspection, multi-file tabs, and side-by-side file comparison.

---

## License

MIT License

Copyright (c) 2026 Matthew Abbott

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## Author

**Matthew Abbott**
Email: mattbachg@gmail.com
