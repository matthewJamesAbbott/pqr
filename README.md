# pqr — Parquet Viewer & Editor
 
<p align="center">
A fast, keyboard-driven terminal UI for inspecting and editing Apache Parquet files.
</p>
> **Work in Progress**
> This repository contains a functional Python prototype built with [`textual`](https://textual.textualize.io/). It is actively being refined to validate the core workflow, data-handling logic, and terminal UX.
 
---
 
## Future Roadmap
 
Once the Python implementation is perfected and the feature set is finalized, **`pqr` will be ported to Rust** and integrated into the **GlassBox suite**. The Rust version will be engineered for maximum performance and correctness, featuring **full validation against 15 CISA/NSA harnesses tested with [`kani`](https://github.com/model-checking/kani)** to guarantee memory safety, type correctness, and enterprise-grade reliability.
 
---
 
## Features
 
- **Terminal UI:** Modern, responsive TUI with zebra-striped tables and a dynamic status bar.
- **Vim-like Navigation:** `j`/`k` for up/down, `h`/`l` for left/right, `g`/`G` for top/bottom, plus `PgUp`/`PgDn`.
- **In-Place Editing:** Edit cells with `i` or `e`, append to values with `a`.
- **Type-Aware Saving:** Automatically converts edited strings back to original Parquet types (`int`, `float`, `bool`, `datetime`, `string`).
- **Safe Workflows:** Tracks edits, warns on unsaved changes, and exports to a new `<filename>_edited.parquet` file.
---
 
## Installation
 
Requires Python 3.9+ and the following dependencies:
 
```bash
pip install pandas pyarrow textual
```
 
---
 
## Usage
 
Run the script directly with a path to a `.parquet` file:
 
```bash
python pqr.py path/to/your/data.parquet
```
 
Or, if installed as a command:
 
```bash
pqr path/to/your/data.parquet
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
| `Enter` / `Ctrl+J` | Confirm edit |
| `Esc` | Cancel edit |
 
### Actions
 
| Key | Action |
|-----|--------|
| `w` | Save to `<filename>_edited.parquet` |
| `q` | Quit (warns if unsaved edits exist) |
 
---
 
## How It Works
 
1. Loads the Parquet file via `pyarrow` into a `pandas.DataFrame`.
2. Renders the data in a `textual` `DataTable` with cursor tracking.
3. Captures cell edits in an overlay screen, preserving original values for undo/reset logic.
4. On save (`w`), applies type-aware conversions and writes a new Parquet file with an `_edited` suffix.
5. Maintains an edit counter in the status bar and warns before quitting with unsaved changes.
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
