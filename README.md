# pqr — Parquet & JSONL Viewer

**v0.x · under active development**

A vim-key terminal viewer and editor for Parquet files and compressed JSONL archives. Opens a 281 GB file in 0.12 seconds. Works on spinning disk.

- Parquet
- .jsonl.zst
- Python 3.9+
- Textual TUI
- vim keybindings
- SQL, filter, export
- MIT License

## Quick Start

```bash
# open any parquet file
$ pqr data.parquet

# open a 281 GB compressed JSONL archive — streams lazily, opens in ~0.1s
$ pqr worldcat.jsonl.zst

# schema without opening the TUI
$ pqr data.parquet --schema

# filter → sort → export as CSV, all in one command
$ pqr data.parquet --step "filter:price > 100" --step "sort:column=price" --export

# SQL query (requires duckdb)
$ pqr data.parquet --sql "SELECT category, COUNT(*) FROM df GROUP BY category"
```

## Value Propositions

**Multi-TB files, instant open**
Two-phase lazy loading: decompress the first 100 MB to get your schema and first 3 000 rows, then stream forward on demand. The file stays on disk — only what you're looking at gets read.

**Lives in the terminal**
No browser, no Jupyter, no GUI. A full Textual TUI with zebra-striped rows, a live status bar, and vim-key navigation. Works over SSH, in tmux, on headless servers.

**Composable step pipeline**
Filter, sort, SQL, Python, shell — steps chain together, output piping to the next. Use them interactively in the TUI, or string them in a one-liner from the CLI.

**Actually editable**
Edit cells, append text, add rows, mark rows for deletion. Type-aware conversion back to the original Parquet or JSONL schema. Save produces `_edited.parquet` or `.edited.jsonl`.

## The Problem It Solves

Large analytical datasets — Open Library dumps, WorldCat metadata, Common Crawl, GDELT, Wikipedia SQL mirrors — are typically distributed as multi-gigabyte or multi-terabyte Zstandard-compressed JSONL files. The standard options are bad:

- **Decompress first:** A 281 GB `.zst` might expand to 2–3 TB. You need the space, the time, and a reason to believe you'll use every row.
- **Load into Pandas:** Crashes or swap-kills on anything that doesn't fit in RAM. Not an option for HDD-hosted archives.
- **grep / jq:** Great for spot-checks, bad for browsing. No column view, no schema, no stats, no edit.
- **pqr:** Stream what you need. See the schema instantly. Browse, filter, and query without touching disk beyond the rows currently on screen. The same philosophy applies to Parquet: files over 5 000 rows use lazy row-group loading, so a 50 M-row table opens just as fast as a 500-row one.

## Lazy Streaming for `.jsonl.zst`

Loading is split into two phases that run automatically:

```
open file → phase 1: sample 100 MB → ~3 000 rows + schema → TUI renders
```

From that point, a single stream reader walks the decompressed output as you scroll. Forward movement fills a 3 000-row sliding window cache. Backward jumps reopen the stream from the beginning — slow for very large backward leaps, instant for anything already cached.

Real-world numbers on the **281 GB worldcat** archive — 14 M rows, 29 columns:

| Action | Time | Notes |
| :--- | :--- | :--- |
| Initial open (first 3 000 rows) | ~0.12 s | Schema inferred, DataTable rendered |
| Next page (rows already cached) | instant | Served from in-memory window |
| Scroll beyond cache | ~0.3–0.5 s | Per page, decompresses the next chunk |
| Schema display (`S`) | instant | Read from Phase 1 sample, no extra IO |
| Export to CSV (`W`) | full decompression | Only operation that reads the whole file |

> **Tip:** For repeated SQL queries on a large `.zst` file, export to Parquet first (`W` → parquet). Parquet's row-group structure lets pqr seek directly to the rows you need without scanning from the start.

### Column schema from nested JSONL

Nested JSON objects are flattened using dot notation during Phase 1. A record like `{"meta": {"isbn": "...", "year": 2003}}` becomes columns `meta.isbn` and `meta.year`. Arrays are serialised as JSON strings and kept in a single column.

## Installation

Requires Python 3.9+. No build step — just install deps and run the script.

**Core (required)**

```bash
pip install pandas pyarrow textual
```

**.zst file support (required for zst)**

```bash
pip install zstandard
```

**SQL queries (optional)**

```bash
pip install duckdb
```

**Excel export (optional)**

```bash
pip install openpyxl
```

**Clipboard yank (optional)**

```bash
pip install pyperclip
```

```bash
# copy to your PATH, or just run directly
chmod +x pqr
cp pqr ~/.local/bin/

# or run without installing
python3 pqr data.parquet
```

## Usage Modes

### Terminal UI

The default. Pass a file, a directory, or nothing at all:

```bash
pqr data.parquet          # open file
pqr archive.jsonl.zst     # open compressed JSONL
pqr data_folder/          # browse directory for .parquet and .zst files
pqr v1.parquet v2.parquet # side-by-side diff
pqr                       # pick from 10 recent files
```

### Batch mode (no TUI)

Add any `--step` flag and pqr skips the UI, runs the pipeline, and prints results to stdout. Good for scripting and shell pipelines:

```bash
pqr data.parquet --schema
pqr data.parquet --filter "page_num >= 100"
pqr data.parquet --step "filter:price > 10" --step "sort:column=price" --export
pqr data.parquet --sql "SELECT * FROM df LIMIT 10"
pqr data.parquet --step "python:len(df)"
pqr data.parquet --step "shell:wc -l"
pqr archive.jsonl.zst --schema    # schema works on .zst too
```

Add `--tui` to any batch command to apply the steps and then open the TUI with the resulting data.

## Step Pipeline

Every operation in pqr is a **step**. Steps take the current data state and produce a new one. Chain them with `--step` flags, or run them interactively in the TUI. Steps use `:` to separate the name from arguments and `;` to separate multiple arguments.

```
parquet / zst → filter → sort → sql → python → export
```

| Step | Syntax | Description |
| :--- | :--- | :--- |
| `schema` | `schema` | Print schema, column types, null counts. zst-aware. |
| `filter` | `filter:col > 10` | Filter rows using pandas query syntax. |
| `sort` | `sort:column=name;desc=true` | Sort by column, ascending or descending. |
| `sql` | `sql:SELECT * FROM df LIMIT 5` | Run DuckDB SQL. `df` is the current dataframe. |
| `stats` | `stats;column=price` | Mean, min, max, std, quartiles, null count. |
| `search` | `search:keyword` | Full-text search across all columns, returns row/col matches. |
| `hide` | `hide:column=name` | Hide a column from the view (toggle). |
| `yank` | `yank:column=price;row=5` | Copy cell or entire column to clipboard. Tries xclip, wl-copy, pbcopy, OSC 52. |
| `export` | `export:format=json;output=out.json` | Export to CSV, JSON, or Parquet. |
| `delete-row` | `delete-row;row=5` | Delete a row by index. |
| `python` | `python:df['col'].sum()` | Evaluate a Python expression. `df` and `pd` available. |
| `shell` | `shell:cut -d, -f1` | Pipe CSV data through any shell command via stdin. |

### Shorthand flags

```bash
--schema            # --step schema
--sql "SELECT ..."  # --step sql:SELECT ...
--filter "col > 5"  # --step filter:col > 5
--sort col_name     # --step sort:column=col_name
--yank col_name     # --step yank:column=col_name
--export            # --step export (always appended last)
```

### Custom shortcuts

Save reusable step sequences to `~/.config/pqr/shortcuts.toml`:

```toml
[shortcuts.summary]
description = "Print schema and stats"
steps = ["schema", "stats"]

[shortcuts.highvalue]
description = "High-value items sorted by price"
steps = ["filter:price > 100", "sort:column=price;desc=true", "export:format=csv"]
```

```bash
pqr data.parquet --shortcut summary
pqr data.parquet --shortcut highvalue
```

## TUI Keybindings

### Navigation

| Key | Action |
| :--- | :--- |
| `j` / `k` | Move cursor up / down |
| `h` / `l` | Move cursor left / right |
| `g` | Jump to top |
| `G` | Jump to bottom |
| `Ctrl+F` | Page down |
| `Ctrl+B` | Page up |
| `Tab` / `gt` | Next tab |
| `Shift+Tab` / `gT` | Previous tab |

### Editing

| Key | Action |
| :--- | :--- |
| `i` / `e` | Edit cell value |
| `a` | Append to cell value |
| `Enter` | Confirm edit |
| `Esc` | Cancel edit |
| `v` | View full cell in popup |
| `y` | Copy cell to clipboard |
| `O` | Add new empty row |
| `dd` | Mark row for deletion |

### Data operations

| Key | Action |
| :--- | :--- |
| `s` | Sort column (toggles asc / desc) |
| `H` | Hide / show current column |
| `x` | Column statistics |
| `f` | Toggle filter bar |
| `/` | Search across all columns |
| `n` / `N` | Next / previous match |
| `:` | SQL query prompt (DuckDB) |
| `S` | View schema |

### File & Export

| Key | Action |
| :--- | :--- |
| `o` / `Ctrl+O` | Open file browser |
| `w` | Save edits to `_edited` file |
| `W` | Export as CSV, Excel, or Parquet |
| `q` | Quit (warns on unsaved edits) |

> **Save behaviour:** For Parquet files, `w` writes `<filename>_edited.parquet`. For `.zst` files, it writes an uncompressed `.edited.jsonl` — the full file is decompressed for the save pass.

## How It Works

### Parquet files

Loaded via `pyarrow` into a `pandas.DataFrame`. Files over 5 000 rows use lazy row-group loading — only the groups needed for the current viewport are read. The `ParquetFile` object stays open so seeking between row groups is cheap.

### `.jsonl.zst` files — LazyJsonlReader

A custom streaming class that mirrors the Parquet lazy-loading pattern for compressed JSONL:

1. **Phase 1 (instant):** Opens the file, decompresses the first 100 MB, and uses the output to infer a flattened column schema (nested JSON keys joined with `.`), estimate total row count from decompressed bytes per MB, and populate the first cache window of 3 000 rows.
2. **Phase 2 (streaming):** A single `stream_reader` walks the decompressed output as you scroll forward, filling a sliding 3 000-row cache. Forward scrolling extends the stream; backward jumps reopen it from the beginning.

### Step pipeline execution

Steps are parsed from CLI specs or TUI prompts into `Step` objects, then dispatched through `_STEP_MAP` to handler functions. Each handler receives a `PipelineState` (current dataframe + metadata) and returns a `StepResult`. The TUI and batch-mode CLI share the same handlers — the only difference is whether results go to the DataTable widget or stdout.

### Cell editing

Edits are held in a dictionary keyed by `(row, col)` index until save. On `w`, the full dataframe is reconstructed (reading all row groups or decompressing the full `.zst` stream), deleted rows are dropped, and edited cells are type-converted back to the original column dtype before writing.

## Supported File Types

| Format | Extension | Read | Write | SQL / filter | Lazy load |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Apache Parquet | `.parquet` | Yes | Yes (`_edited.parquet`) | Yes | Yes (row-group) |
| Compressed JSONL | `.jsonl.zst` | Yes | Yes (`.edited.jsonl`) | Yes (full decomp) | Yes (streaming) |

---

**Matthew Abbott** · mattbachg@gmail.com

MIT License · Copyright © 2026 Matthew Abbott
