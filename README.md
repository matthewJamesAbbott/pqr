# pqr — Parquet & JSONL Viewer & Editor

A fast, keyboard-driven terminal application for inspecting, querying, and editing Apache Parquet and zstandard-compressed JSONL (`.zst`) files. Built on the [Textual](https://textual.textualize.io/) TUI framework. Think of it as "less" or "vim" but for columnar data.

# <still under development>

![PQR 1](pqr1.png)

![PQR 2](pqr2.png)

![PQR 3](pqr3.png)

![PQR 4](pqr4.png)

---

## Quick Start

```bash
# Open a parquet file in the terminal UI
pqr data.parquet

# Open a compressed JSONL file
pqr data.jsonl.zst

# Print schema without the UI
pqr data.parquet --schema

# Filter, sort, and export as CSV
pqr data.parquet --step "filter:price > 100" --step "sort:column=price" --export

# Run a SQL query
pqr data.parquet --sql "SELECT category, COUNT(*) FROM df GROUP BY category"

# View recent files (last 10 opened)
pqr

# Compare two parquet files side by side
pqr file_v1.parquet file_v2.parquet

# Browse a directory of .parquet and .zst files
pqr data_folder/
```

---

## Supported File Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| Apache Parquet | `.parquet` | Full schema metadata, row groups, statistics |
| Compressed JSONL | `.zst` | Zstandard-compressed JSON lines, lazy-streaming reader |

Both formats support all TUI features: navigation, editing, search, filtering, sorting, SQL, stats, and export.

---

## Installation

Requires Python 3.9+ and the following core dependencies:

```bash
pip install pandas pyarrow textual
```

Optional dependencies:

```bash
# SQL query mode (press : in the TUI or use --sql)
pip install duckdb

# Excel export support
pip install openpyxl

# Compressed JSONL (.zst) file support
pip install zstandard
```

No external clipboard dependency is required. `pqr` uses a robust multi-backend clipboard system (xclip, wl-copy, pbcopy, clip, OSC 52) with tmux and SSH awareness, requiring no extra packages.

Copy or symlink `pqr` to a directory in your `PATH`, or run it directly:

```bash
python3 pqr data.parquet
```

---

## Usage Modes

### Terminal UI (default)

```bash
pqr data.parquet          # Open a single file
pqr                       # Recent files picker
pqr data_folder/          # Browse .parquet and .zst files in a directory
pqr v1.parquet v2.parquet # Side-by-side diff view
```

The TUI provides full vim-style navigation, cell editing, in-place search, column filtering, sorting, statistics, SQL queries, export, and clipboard integration.

### Batch Mode (no TUI)

Add `--step` or `--steps` to run operations and print results to stdout without opening the UI:

```bash
# Print schema
pqr data.parquet --schema

# Filter then print CSV
pqr data.parquet --filter "page_num >= 100"

# Chain multiple steps
pqr data.parquet --step "filter:price > 10" --step "sort:column=price" --export

# SQL query (requires duckdb)
pqr data.parquet --sql "SELECT * FROM df LIMIT 10"

# Run custom Python
pqr data.parquet --step "python:len(df)"

# Pipe to shell commands
pqr data.parquet --step "shell:wc -l"

# Force TUI after batch steps
pqr data.parquet --step "filter:price > 100" --tui
```

Run `pqr --help` for the full list of CLI options.

---

## Built-in Steps

Steps are the basic units of operation. Each step takes the current data and produces a result. Steps can be chained: the output of one becomes the input of the next.

| Step | Syntax | Description |
|------|--------|-------------|
| `schema` | `schema` | Print schema, column metadata, null counts, compression info |
| `yank` | `yank:column=name;row=0` | Copy cell or entire column to clipboard |
| `search` | `search:keyword` | Search text across all columns |
| `filter` | `filter:col > 10` | Filter rows (pandas query syntax) |
| `sort` | `sort:column=name;desc=true` | Sort by column (asc/desc) |
| `hide` | `hide:column=name` | Hide a column from view |
| `sql` | `sql:SELECT * FROM df` | Run DuckDB SQL query |
| `stats` | `stats;column=name` | Show column statistics (count, mean, min, max) |
| `delete-row` | `delete-row;row=5` | Delete a row by index |
| `export` | `export:format=json;output=out.json` | Export to CSV, JSON, or Parquet |
| `python` | `python:df['col'].sum()` | Evaluate a Python expression (has `df`, `pd`) |
| `shell` | `shell:cut -d, -f1` | Pipe CSV data through a shell command |

Steps use `:` to separate the step name from arguments, and `;` to separate multiple arguments:

```bash
# yank row 5 of column "price"
--step "yank:column=price;row=5"

# export to JSON file
--step "export:format=json;output=report.json"
```

### Shorthand flags

Common steps have shorthand CLI flags as shortcuts:

```bash
--schema              # --step schema
--sql "SELECT ..."    # --step sql:SELECT ...
--filter "col > 5"    # --step filter:col > 5
--sort col_name       # --step sort:column=col_name
--yank col_name       # --step yank:column=col_name
--export              # --step export (appended last)
```

---

## Custom Shortcuts

Store reusable step sequences in `~/.config/pqr/shortcuts.toml`:

```toml
[shortcuts.summary]
description = "Print schema and stats"
steps = ["schema", "stats"]

[shortcuts.highvalue]
description = "High-value items sorted by price"
steps = ["filter:price > 100", "sort:column=price;desc=true", "export:format=csv"]

[shortcuts.textpages]
description = "Pages that contain text"
steps = ["python:len(df[df['text'].notna()])"]
```

Use them with `--shortcut`:

```bash
pqr data.parquet --shortcut summary
pqr data.parquet --shortcut highvalue
```

---

## Terminal UI

When opened without `--step`, pqr launches the full interactive terminal UI.

### Navigation

| Key | Action |
|-----|--------|
| `j` / `k` or `↑` / `↓` | Move cursor up/down |
| `h` / `l` or `←` / `→` | Move cursor left/right |
| `g` | Jump to top of file |
| `G` | Jump to bottom of file |
| `Ctrl+G` | Jump to a specific row number |
| `Ctrl+F` / `PgDn` | Page down |
| `Ctrl+B` / `PgUp` | Page up |

For large files (5,000+ rows for Parquet, all `.zst` files), pqr uses **lazy loading**: only the visible rows are loaded into memory, streaming data on-demand as you scroll.

### Editing

| Key | Action |
|-----|--------|
| `i` / `e` | Edit cell value in a popup |
| `a` | Append to cell value (cursor at end) |
| `v` | View full cell contents in a scrollable popup |
| `y` | Copy cell value to clipboard (yank) |
| `O` | Add a new empty row at the end |
| `dd` | Mark current row for deletion |

Edits are tracked in-memory. Type-aware conversion ensures values are cast back to the correct data type on save.

### Saving

| Key | Action |
|-----|--------|
| `w` | Save edits to `<filename>_edited.parquet` (or `.edited.jsonl` for `.zst` files) |

Saves apply all cell edits, type conversions, and row deletions to a new file alongside the original.

### Data Operations

| Key | Action |
|-----|--------|
| `s` | Sort current column (toggles ascending/descending) |
| `H` | Hide or show current column (toggle) |
| `x` | Show column statistics in status bar |
| `/` | Search across all columns (vim-style prompt) |
| `n` / `N` | Next / previous search match |
| `f` | Toggle filter bar (pandas query syntax, e.g. `col == value`) |
| `:` | SQL query prompt (requires duckdb) |

### File Operations

| Key | Action |
|-----|--------|
| `o` / `Ctrl+O` | Open file browser (directory tree) |
| `W` | Export as CSV, Excel (`.xlsx`), or Parquet |
| `S` | View full schema details (types, null counts, encodings, compressed sizes) |

### Tabs

| Key | Action |
|-----|--------|
| `Tab` / `gt` | Next tab |
| `Shift+Tab` / `gT` | Previous tab |

Open multiple files in tabs by using the file browser (`o`). Switch between tabs to compare datasets without quitting.

### Other

| Key | Action |
|-----|--------|
| `q` | Quit (warns if unsaved edits exist) |

---

## Side-by-Side Diff

Open two Parquet files simultaneously to see a side-by-side comparison:

```bash
pqr file_v1.parquet file_v2.parquet
```

This displays both files in split panels, aligning all columns (including columns unique to one file).

---

## Recent Files

pqr tracks the last 10 files you've opened (stored in `~/.pqr_history`). Run `pqr` with no arguments to bring up the recent files picker, with file existence status shown for each entry. From the recent files screen, you can also launch the file browser to find new files.

---

## Compressed JSONL (`.zst`) Support

`.zst` files (zstandard-compressed JSON lines) are supported with a **lazy streaming reader** that:

1. **Samples the first 100 MB** of compressed data to build an instant index: row count, column names, and data types.
2. **Streams rows on-demand** as you navigate, without decompressing the entire file.
3. **Flattens nested JSON** into dot-separated column names (e.g. `user.name`, `items.price`).
4. **Handles arrays** by serializing them as JSON strings.
5. Supports all TUI operations: search, filter, sort, stats, SQL, edit, and export.

Install with `pip install zstandard`.

---

## Clipboard (Yank)

The `y` key (and `yank` step) copies cell values to your system clipboard using a robust fallback chain:

1. **xclip** (Linux X11)
2. **wl-copy** (Linux Wayland)
3. **pbcopy** (macOS)
4. **clip** (Windows)
5. **OSC 52** terminal sequence (works over SSH, tmux, and in multiplexers)

No extra Python packages are needed. The OSC 52 fallback automatically detects tmux client TTY and SSH sessions.

---

## Status Bar

The bottom status bar shows live context as you navigate:

- File name, row count, column count
- Current row position (`row 42/10000`)
- Current column name
- Edit count
- Live numeric column stats: mean, min, max, null count

---

## How It Works

1. Loads `.parquet` files via `pyarrow` into a `pandas.DataFrame`. Files over 5,000 rows use **lazy row-group loading** for efficient memory usage.
2. Loads `.zst` files with a **streaming reader** that indexes the file header then fills a row cache on-demand.
3. Renders data in a `textual` `DataTable` with cursor tracking and zebra-striped rows.
4. Cell edits are captured in an overlay screen with **type-aware conversion** back to the original data type.
5. The **step pipeline** processes operations sequentially: each step receives the current data state and produces a new one. The same pipeline powers both the CLI batch mode and TUI commands.
6. On save (`w`), marked rows are dropped and edited cells are type-converted before writing to a new `<filename>_edited.parquet` (or `.edited.jsonl`) file.
7. Search (`/`) highlights matches across the visible viewport and lets you navigate between them with `n`/`N`.

---

## License

MIT License. Copyright (c) 2026 Matthew Abbott.

## Author

**Matthew Abbott** — mattbachg@gmail.com
