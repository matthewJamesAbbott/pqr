<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pqr — Parquet &amp; JSONL Viewer</title>
<style>
  :root {
    --bg: #0e0e0e;
    --bg2: #161616;
    --bg3: #1f1f1f;
    --border: #2a2a2a;
    --text: #d4d2c8;
    --muted: #6b6a65;
    --dim: #3a3a38;
    --accent: #e8a44a;
    --accent2: #5db8a0;
    --accent3: #7e7cdb;
    --hot: #d05538;
    --code-bg: #1a1a1a;
    --mono: "JetBrains Mono", "Fira Code", ui-monospace, monospace;
    --sans: -apple-system, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.7;
    max-width: 900px;
    margin: 0 auto;
    padding: 0 2rem 6rem;
  }

  /* ── Hero ─────────────────────────────────────────────── */
  .hero {
    padding: 5rem 0 3.5rem;
    border-bottom: 1px solid var(--border);
    position: relative;
  }
  .hero-eyebrow {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--accent);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 1rem;
  }
  .hero h1 {
    font-size: clamp(2.6rem, 6vw, 4rem);
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1.05;
    color: #fff;
  }
  .hero h1 .dim { color: var(--muted); }
  .hero-sub {
    margin-top: 1.25rem;
    font-size: 1.1rem;
    color: var(--muted);
    max-width: 580px;
    line-height: 1.6;
  }
  .hero-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 1.75rem;
  }
  .badge {
    font-family: var(--mono);
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--border);
    color: var(--muted);
    white-space: nowrap;
  }
  .badge.hot { border-color: var(--hot); color: var(--hot); }
  .badge.green { border-color: var(--accent2); color: var(--accent2); }
  .badge.purple { border-color: var(--accent3); color: var(--accent3); }

  /* ── Terminal mockup ──────────────────────────────────── */
  .terminal {
    background: #0a0a0a;
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin: 3rem 0;
    font-family: var(--mono);
    font-size: 12.5px;
    line-height: 1.6;
  }
  .terminal-bar {
    background: #1c1c1c;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
  }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .dot.r { background: #ff5f56; }
  .dot.y { background: #ffbd2e; }
  .dot.g { background: #27c93f; }
  .terminal-title {
    margin-left: auto;
    font-size: 11px;
    color: var(--muted);
  }
  .terminal-body { padding: 20px 24px; overflow-x: auto; }
  .terminal-body pre { white-space: pre; }

  .c-prompt { color: var(--accent2); }
  .c-cmd    { color: #fff; }
  .c-kw     { color: var(--accent); }
  .c-str    { color: #9ecc6f; }
  .c-num    { color: #8fb8d4; }
  .c-dim    { color: var(--muted); }
  .c-hot    { color: var(--hot); }
  .c-purple { color: var(--accent3); }
  .c-ok     { color: var(--accent2); }
  .c-head   { color: var(--accent); font-weight: 600; }
  .c-row    { color: #b0cecc; }
  .c-row-alt{ color: #8fb0ae; }

  /* ── Value prop cards ─────────────────────────────────── */
  .value-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 1px;
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin: 2.5rem 0;
  }
  .value-card {
    background: var(--bg2);
    padding: 1.5rem;
    transition: background 0.15s;
  }
  .value-card:hover { background: var(--bg3); }
  .value-icon {
    font-size: 1.4rem;
    margin-bottom: 0.75rem;
  }
  .value-title {
    font-size: 14px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 0.4rem;
    letter-spacing: -0.01em;
  }
  .value-body {
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
  }

  /* ── Section layout ───────────────────────────────────── */
  .section { padding: 3.5rem 0; border-bottom: 1px solid var(--border); }
  .section:last-child { border-bottom: none; }
  h2 {
    font-size: 1.5rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #fff;
    margin-bottom: 1.25rem;
  }
  h3 {
    font-size: 1rem;
    font-weight: 600;
    color: var(--text);
    margin: 1.75rem 0 0.6rem;
  }
  p { margin-bottom: 1rem; color: var(--muted); }
  p:last-child { margin-bottom: 0; }

  /* ── Inline code & blocks ─────────────────────────────── */
  code {
    font-family: var(--mono);
    font-size: 12px;
    background: var(--code-bg);
    border: 1px solid var(--border);
    padding: 2px 6px;
    border-radius: 4px;
    color: var(--accent);
  }
  pre {
    background: #0a0a0a;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.25rem 1.5rem;
    overflow-x: auto;
    margin: 1rem 0;
  }
  pre code {
    background: none;
    border: none;
    padding: 0;
    font-size: 12.5px;
    color: var(--text);
  }
  .sh-comment { color: var(--muted); }
  .sh-cmd     { color: var(--accent2); }
  .sh-arg     { color: #fff; }
  .sh-flag    { color: var(--accent3); }
  .sh-str     { color: #9ecc6f; }

  /* ── Performance table ───────────────────────────────── */
  .perf-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--mono);
    font-size: 13px;
    margin: 1.5rem 0;
  }
  .perf-table th {
    text-align: left;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-weight: 500;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .perf-table td {
    padding: 10px 16px;
    border-bottom: 1px solid var(--dim);
    color: var(--text);
    vertical-align: top;
  }
  .perf-table tr:last-child td { border-bottom: none; }
  .perf-table tr:hover td { background: var(--bg2); }
  .perf-value { color: var(--accent2); font-weight: 600; }
  .perf-label { color: var(--muted); font-size: 11px; display: block; margin-top: 2px; }

  /* ── Keybinding table ─────────────────────────────────── */
  .keys {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 2rem;
    margin: 1rem 0;
  }
  .key-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 7px 0;
    border-bottom: 1px solid var(--dim);
  }
  .key-row:last-child { border-bottom: none; }
  kbd {
    font-family: var(--mono);
    font-size: 11px;
    background: var(--bg3);
    border: 1px solid var(--border);
    border-bottom-width: 2px;
    border-radius: 4px;
    padding: 2px 7px;
    color: #fff;
    white-space: nowrap;
    flex-shrink: 0;
    min-width: 52px;
    text-align: center;
  }
  .key-desc {
    font-size: 13px;
    color: var(--muted);
  }

  /* ── Step table ──────────────────────────────────────── */
  .step-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 1.25rem 0;
  }
  .step-table th {
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 500;
  }
  .step-table td {
    padding: 9px 12px;
    border-bottom: 1px solid var(--dim);
    color: var(--muted);
    vertical-align: top;
  }
  .step-table td:first-child code { color: var(--accent2); }
  .step-table td:nth-child(2) code { color: var(--accent3); font-size: 11px; }
  .step-table tr:last-child td { border-bottom: none; }

  /* ── Pipeline diagram ────────────────────────────────── */
  .pipeline {
    display: flex;
    align-items: center;
    gap: 0;
    margin: 1.5rem 0;
    flex-wrap: wrap;
    gap: 4px;
  }
  .pipe-step {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 14px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--accent2);
  }
  .pipe-arrow {
    color: var(--muted);
    font-size: 16px;
    padding: 0 2px;
  }

  /* ── Install block ───────────────────────────────────── */
  .install-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin: 1.25rem 0;
  }
  @media (max-width: 600px) {
    .install-grid { grid-template-columns: 1fr; }
    .keys { grid-template-columns: 1fr; }
  }
  .install-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
  }
  .install-card .label {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.5rem;
  }
  .install-card pre {
    margin: 0;
    border: none;
    background: none;
    padding: 0;
    font-size: 12px;
  }

  /* ── Warning / note callout ─────────────────────────── */
  .note {
    border-left: 3px solid var(--accent);
    background: #1a1600;
    border-radius: 0 6px 6px 0;
    padding: 0.75rem 1rem;
    margin: 1.25rem 0;
    font-size: 13px;
    color: #c8a860;
  }
  .note strong { color: var(--accent); }

  /* ── Footer ──────────────────────────────────────────── */
  .footer {
    padding: 3rem 0 0;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .footer-name { font-size: 13px; color: var(--muted); }
  .footer-name a { color: var(--muted); text-decoration: underline; }
  .footer-license {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--dim);
  }

  /* ── Separator label ─────────────────────────────────── */
  .sep {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 2rem 0 1.5rem;
  }
  .sep-line { flex: 1; height: 1px; background: var(--border); }
  .sep-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; white-space: nowrap; }
</style>
</head>
<body>

<!-- ═══════════════════════════════════════════════════════
     HERO
═══════════════════════════════════════════════════════════ -->
<div class="hero">
  <div class="hero-eyebrow">v0.x · under active development</div>
  <h1>pqr<span class="dim"> /</span><br>parquet reader</h1>
  <p class="hero-sub">A vim-key terminal viewer and editor for Parquet files and compressed JSONL archives. Opens a 281 GB file in 0.12 seconds. Works on spinning disk.</p>
  <div class="hero-badges">
    <span class="badge hot">Parquet</span>
    <span class="badge hot">.jsonl.zst</span>
    <span class="badge green">Python 3.9+</span>
    <span class="badge green">Textual TUI</span>
    <span class="badge purple">vim keybindings</span>
    <span class="badge purple">SQL · filter · export</span>
    <span class="badge">MIT License</span>
  </div>
</div>


<!-- ═══════════════════════════════════════════════════════
     QUICK START TERMINAL
═══════════════════════════════════════════════════════════ -->
<div class="terminal" style="margin-top: 3rem;">
  <div class="terminal-bar">
    <div class="dot r"></div><div class="dot y"></div><div class="dot g"></div>
    <span class="terminal-title">quick start</span>
  </div>
  <div class="terminal-body"><pre><span class="c-dim"># open any parquet file</span>
<span class="c-prompt">$</span> <span class="c-cmd">pqr</span> data.parquet

<span class="c-dim"># open a 281 GB compressed JSONL archive — streams lazily, opens in ~0.1s</span>
<span class="c-prompt">$</span> <span class="c-cmd">pqr</span> worldcat.jsonl.zst

<span class="c-dim"># schema without opening the TUI</span>
<span class="c-prompt">$</span> <span class="c-cmd">pqr</span> data.parquet <span class="c-kw">--schema</span>

<span class="c-dim"># filter → sort → export as CSV, all in one command</span>
<span class="c-prompt">$</span> <span class="c-cmd">pqr</span> data.parquet <span class="c-kw">--step</span> <span class="c-str">"filter:price &gt; 100"</span> <span class="c-kw">--step</span> <span class="c-str">"sort:column=price"</span> <span class="c-kw">--export</span>

<span class="c-dim"># SQL query (requires duckdb)</span>
<span class="c-prompt">$</span> <span class="c-cmd">pqr</span> data.parquet <span class="c-kw">--sql</span> <span class="c-str">"SELECT category, COUNT(*) FROM df GROUP BY category"</span></pre></div>
</div>


<!-- ═══════════════════════════════════════════════════════
     VALUE PROPS
═══════════════════════════════════════════════════════════ -->
<div class="value-grid">
  <div class="value-card">
    <div class="value-icon">⚡</div>
    <div class="value-title">Multi-TB files, instant open</div>
    <div class="value-body">Two-phase lazy loading: decompress the first 100 MB to get your schema and first 3 000 rows, then stream forward on demand. The file stays on disk — only what you're looking at gets read.</div>
  </div>
  <div class="value-card">
    <div class="value-icon">🖥</div>
    <div class="value-title">Lives in the terminal</div>
    <div class="value-body">No browser, no Jupyter, no GUI. A full Textual TUI with zebra-striped rows, a live status bar, and vim-key navigation. Works over SSH, in tmux, on headless servers.</div>
  </div>
  <div class="value-card">
    <div class="value-icon">🔗</div>
    <div class="value-title">Composable step pipeline</div>
    <div class="value-body">Filter, sort, SQL, Python, shell — steps chain together, output piping to the next. Use them interactively in the TUI, or string them in a one-liner from the CLI.</div>
  </div>
  <div class="value-card">
    <div class="value-icon">✏️</div>
    <div class="value-title">Actually editable</div>
    <div class="value-body">Edit cells, append text, add rows, mark rows for deletion. Type-aware conversion back to the original Parquet or JSONL schema. Save produces <code>_edited.parquet</code> or <code>.edited.jsonl</code>.</div>
  </div>
</div>


<!-- ═══════════════════════════════════════════════════════
     WHY ZST + PARQUET MATTERS
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>The problem it solves</h2>
  <p>Large analytical datasets — Open Library dumps, WorldCat metadata, Common Crawl, GDELT, Wikipedia SQL mirrors — are typically distributed as multi-gigabyte or multi-terabyte Zstandard-compressed JSONL files. The standard options are bad:</p>

  <div class="value-grid" style="margin-bottom: 1.5rem;">
    <div class="value-card" style="border-left: 3px solid var(--hot);">
      <div class="value-title" style="color: var(--hot);">Decompress first</div>
      <div class="value-body">A 281 GB <code>.zst</code> might expand to 2–3 TB. You need the space, the time, and a reason to believe you'll use every row.</div>
    </div>
    <div class="value-card" style="border-left: 3px solid var(--hot);">
      <div class="value-title" style="color: var(--hot);">Load into Pandas</div>
      <div class="value-body">Crashes or swap-kills on anything that doesn't fit in RAM. Not an option for HDD-hosted archives.</div>
    </div>
    <div class="value-card" style="border-left: 3px solid var(--hot);">
      <div class="value-title" style="color: var(--hot);">grep / jq</div>
      <div class="value-body">Great for spot-checks, bad for browsing. No column view, no schema, no stats, no edit.</div>
    </div>
    <div class="value-card" style="border-left: 3px solid var(--accent2);">
      <div class="value-title" style="color: var(--accent2);">pqr</div>
      <div class="value-body">Stream what you need. See the schema instantly. Browse, filter, and query without touching disk beyond the rows currently on screen.</div>
    </div>
  </div>

  <p>The same philosophy applies to Parquet: files over 5 000 rows use lazy row-group loading, so a 50 M-row table opens just as fast as a 500-row one.</p>
</div>


<!-- ═══════════════════════════════════════════════════════
     ZST PERFORMANCE
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>Lazy streaming for <code>.jsonl.zst</code></h2>
  <p>Loading is split into two phases that run automatically:</p>

  <div class="pipeline" style="margin-bottom: 1.5rem;">
    <div class="pipe-step">open file</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step" style="color: var(--accent);">phase 1 · sample 100 MB</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step" style="color: var(--accent);">~3 000 rows + schema</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step" style="color: var(--accent2);">TUI renders</div>
  </div>

  <p style="color: var(--muted); font-size: 13px; margin-bottom: 1.5rem;">From that point, a single stream reader walks the decompressed output as you scroll. Forward movement fills a 3 000-row sliding window cache. Backward jumps reopen the stream from the beginning — slow for very large backward leaps, instant for anything already cached.</p>

  <p>Real-world numbers on the <strong style="color: var(--text);">281 GB worldcat</strong> archive — 14 M rows, 29 columns, on spinning HDD:</p>

  <table class="perf-table">
    <thead>
      <tr>
        <th>Action</th>
        <th>Time</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Initial open (first 3 000 rows)</td>
        <td><span class="perf-value">~0.12 s</span></td>
        <td style="font-size: 12px; color: var(--muted);">Schema inferred, DataTable rendered</td>
      </tr>
      <tr>
        <td>Next page (rows already cached)</td>
        <td><span class="perf-value">instant</span></td>
        <td style="font-size: 12px; color: var(--muted);">Served from in-memory window</td>
      </tr>
      <tr>
        <td>Scroll beyond cache</td>
        <td><span class="perf-value">~0.3–0.5 s</span></td>
        <td style="font-size: 12px; color: var(--muted);">Per page, decompresses the next chunk</td>
      </tr>
      <tr>
        <td>Schema display (<code>S</code>)</td>
        <td><span class="perf-value">instant</span></td>
        <td style="font-size: 12px; color: var(--muted);">Read from Phase 1 sample, no extra IO</td>
      </tr>
      <tr>
        <td>Export to CSV (<code>W</code>)</td>
        <td style="color: var(--muted);">full decompression</td>
        <td style="font-size: 12px; color: var(--muted);">Only operation that reads the whole file</td>
      </tr>
    </tbody>
  </table>

  <div class="note">
    <strong>Tip:</strong> For repeated SQL queries on a large <code>.zst</code> file, export to Parquet first (<kbd>W</kbd> → parquet). Parquet's row-group structure lets pqr seek directly to the rows you need without scanning from the start.
  </div>

  <h3>Column schema from nested JSONL</h3>
  <p>Nested JSON objects are flattened using dot notation during Phase 1. A record like <code>{"meta": {"isbn": "...", "year": 2003}}</code> becomes columns <code>meta.isbn</code> and <code>meta.year</code>. Arrays are serialised as JSON strings and kept in a single column.</p>
</div>


<!-- ═══════════════════════════════════════════════════════
     INSTALLATION
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>Installation</h2>
  <p>Requires Python 3.9+. No build step — just install deps and run the script.</p>

  <div class="install-grid">
    <div class="install-card">
      <div class="label">Core (required)</div>
      <pre><code><span class="sh-cmd">pip install</span> <span class="sh-arg">pandas pyarrow textual</span></code></pre>
    </div>
    <div class="install-card">
      <div class="label">.zst file support (required for zst)</div>
      <pre><code><span class="sh-cmd">pip install</span> <span class="sh-arg">zstandard</span></code></pre>
    </div>
    <div class="install-card">
      <div class="label">SQL queries (optional)</div>
      <pre><code><span class="sh-cmd">pip install</span> <span class="sh-arg">duckdb</span></code></pre>
    </div>
    <div class="install-card">
      <div class="label">Excel export (optional)</div>
      <pre><code><span class="sh-cmd">pip install</span> <span class="sh-arg">openpyxl</span></code></pre>
    </div>
    <div class="install-card">
      <div class="label">Clipboard yank (optional)</div>
      <pre><code><span class="sh-cmd">pip install</span> <span class="sh-arg">pyperclip</span></code></pre>
    </div>
  </div>

  <pre><code><span class="sh-comment"># copy to your PATH, or just run directly</span>
<span class="sh-cmd">chmod +x</span> <span class="sh-arg">pqr</span>
<span class="sh-cmd">cp</span> <span class="sh-arg">pqr ~/.local/bin/</span>

<span class="sh-comment"># or run without installing</span>
<span class="sh-cmd">python3 pqr</span> <span class="sh-arg">data.parquet</span></code></pre>
</div>


<!-- ═══════════════════════════════════════════════════════
     USAGE MODES
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>Usage modes</h2>

  <h3>Terminal UI</h3>
  <p>The default. Pass a file, a directory, or nothing at all:</p>
  <pre><code><span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span>              <span class="sh-comment"># open file</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">archive.jsonl.zst</span>         <span class="sh-comment"># open compressed JSONL</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data_folder/</span>              <span class="sh-comment"># browse directory for .parquet and .zst files</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">v1.parquet v2.parquet</span>     <span class="sh-comment"># side-by-side diff</span>
<span class="sh-cmd">pqr</span>                            <span class="sh-comment"># pick from 10 recent files</span></code></pre>

  <h3>Batch mode (no TUI)</h3>
  <p>Add any <code>--step</code> flag and pqr skips the UI, runs the pipeline, and prints results to stdout. Good for scripting and shell pipelines:</p>
  <pre><code><span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--schema</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--filter</span> <span class="sh-str">"page_num >= 100"</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--step</span> <span class="sh-str">"filter:price > 10"</span> <span class="sh-flag">--step</span> <span class="sh-str">"sort:column=price"</span> <span class="sh-flag">--export</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--sql</span> <span class="sh-str">"SELECT * FROM df LIMIT 10"</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--step</span> <span class="sh-str">"python:len(df)"</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--step</span> <span class="sh-str">"shell:wc -l"</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">archive.jsonl.zst</span> <span class="sh-flag">--schema</span>   <span class="sh-comment"># schema works on .zst too</span></code></pre>

  <p>Add <code>--tui</code> to any batch command to apply the steps and <em>then</em> open the TUI with the resulting data.</p>
</div>


<!-- ═══════════════════════════════════════════════════════
     STEP PIPELINE
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>Step pipeline</h2>
  <p>Every operation in pqr is a <strong style="color: var(--text);">step</strong>. Steps take the current data state and produce a new one. Chain them with <code>--step</code> flags, or run them interactively in the TUI. Steps use <code>:</code> to separate the name from arguments and <code>;</code> to separate multiple arguments.</p>

  <div class="pipeline">
    <div class="pipe-step">parquet / zst</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step">filter</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step">sort</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step">sql</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step">python</div>
    <div class="pipe-arrow">→</div>
    <div class="pipe-step">export</div>
  </div>

  <table class="step-table">
    <thead>
      <tr><th>Step</th><th>Syntax</th><th>Description</th></tr>
    </thead>
    <tbody>
      <tr><td><code>schema</code></td><td><code>schema</code></td><td style="color:var(--muted);">Print schema, column types, null counts. zst-aware.</td></tr>
      <tr><td><code>filter</code></td><td><code>filter:col > 10</code></td><td style="color:var(--muted);">Filter rows using pandas query syntax.</td></tr>
      <tr><td><code>sort</code></td><td><code>sort:column=name;desc=true</code></td><td style="color:var(--muted);">Sort by column, ascending or descending.</td></tr>
      <tr><td><code>sql</code></td><td><code>sql:SELECT * FROM df LIMIT 5</code></td><td style="color:var(--muted);">Run DuckDB SQL. <code>df</code> is the current dataframe.</td></tr>
      <tr><td><code>stats</code></td><td><code>stats;column=price</code></td><td style="color:var(--muted);">Mean, min, max, std, quartiles, null count.</td></tr>
      <tr><td><code>search</code></td><td><code>search:keyword</code></td><td style="color:var(--muted);">Full-text search across all columns, returns row/col matches.</td></tr>
      <tr><td><code>hide</code></td><td><code>hide:column=name</code></td><td style="color:var(--muted);">Hide a column from the view (toggle).</td></tr>
      <tr><td><code>yank</code></td><td><code>yank:column=price;row=5</code></td><td style="color:var(--muted);">Copy cell or entire column to clipboard. Tries xclip, wl-copy, pbcopy, OSC 52.</td></tr>
      <tr><td><code>export</code></td><td><code>export:format=json;output=out.json</code></td><td style="color:var(--muted);">Export to CSV, JSON, or Parquet.</td></tr>
      <tr><td><code>delete-row</code></td><td><code>delete-row;row=5</code></td><td style="color:var(--muted);">Delete a row by index.</td></tr>
      <tr><td><code>python</code></td><td><code>python:df['col'].sum()</code></td><td style="color:var(--muted);">Evaluate a Python expression. <code>df</code> and <code>pd</code> available.</td></tr>
      <tr><td><code>shell</code></td><td><code>shell:cut -d, -f1</code></td><td style="color:var(--muted);">Pipe CSV data through any shell command via stdin.</td></tr>
    </tbody>
  </table>

  <h3>Shorthand flags</h3>
  <pre><code><span class="sh-flag">--schema</span>              <span class="sh-comment"># --step schema</span>
<span class="sh-flag">--sql</span> <span class="sh-str">"SELECT ..."</span>    <span class="sh-comment"># --step sql:SELECT ...</span>
<span class="sh-flag">--filter</span> <span class="sh-str">"col > 5"</span>  <span class="sh-comment"># --step filter:col > 5</span>
<span class="sh-flag">--sort</span> <span class="sh-arg">col_name</span>       <span class="sh-comment"># --step sort:column=col_name</span>
<span class="sh-flag">--yank</span> <span class="sh-arg">col_name</span>       <span class="sh-comment"># --step yank:column=col_name</span>
<span class="sh-flag">--export</span>              <span class="sh-comment"># --step export (always appended last)</span></code></pre>

  <h3>Custom shortcuts</h3>
  <p>Save reusable step sequences to <code>~/.config/pqr/shortcuts.toml</code>:</p>
  <pre><code><span class="sh-comment">[shortcuts.summary]</span>
<span class="sh-arg">description</span> = <span class="sh-str">"Print schema and stats"</span>
<span class="sh-arg">steps</span> = [<span class="sh-str">"schema"</span>, <span class="sh-str">"stats"</span>]

<span class="sh-comment">[shortcuts.highvalue]</span>
<span class="sh-arg">description</span> = <span class="sh-str">"High-value items sorted by price"</span>
<span class="sh-arg">steps</span> = [<span class="sh-str">"filter:price > 100"</span>, <span class="sh-str">"sort:column=price;desc=true"</span>, <span class="sh-str">"export:format=csv"</span>]</code></pre>

  <pre><code><span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--shortcut</span> <span class="sh-arg">summary</span>
<span class="sh-cmd">pqr</span> <span class="sh-arg">data.parquet</span> <span class="sh-flag">--shortcut</span> <span class="sh-arg">highvalue</span></code></pre>
</div>


<!-- ═══════════════════════════════════════════════════════
     KEYBINDINGS
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>TUI keybindings</h2>

  <h3>Navigation</h3>
  <div class="keys">
    <div>
      <div class="key-row"><kbd>j / k</kbd><span class="key-desc">Move cursor up / down</span></div>
      <div class="key-row"><kbd>h / l</kbd><span class="key-desc">Move cursor left / right</span></div>
      <div class="key-row"><kbd>g</kbd><span class="key-desc">Jump to top</span></div>
      <div class="key-row"><kbd>G</kbd><span class="key-desc">Jump to bottom</span></div>
    </div>
    <div>
      <div class="key-row"><kbd>Ctrl+F</kbd><span class="key-desc">Page down</span></div>
      <div class="key-row"><kbd>Ctrl+B</kbd><span class="key-desc">Page up</span></div>
      <div class="key-row"><kbd>Tab / gt</kbd><span class="key-desc">Next tab</span></div>
      <div class="key-row"><kbd>⇧Tab / gT</kbd><span class="key-desc">Previous tab</span></div>
    </div>
  </div>

  <h3>Editing</h3>
  <div class="keys">
    <div>
      <div class="key-row"><kbd>i / e</kbd><span class="key-desc">Edit cell value</span></div>
      <div class="key-row"><kbd>a</kbd><span class="key-desc">Append to cell value</span></div>
      <div class="key-row"><kbd>Enter</kbd><span class="key-desc">Confirm edit</span></div>
      <div class="key-row"><kbd>Esc</kbd><span class="key-desc">Cancel edit</span></div>
    </div>
    <div>
      <div class="key-row"><kbd>v</kbd><span class="key-desc">View full cell in popup</span></div>
      <div class="key-row"><kbd>y</kbd><span class="key-desc">Copy cell to clipboard</span></div>
      <div class="key-row"><kbd>O</kbd><span class="key-desc">Add new empty row</span></div>
      <div class="key-row"><kbd>dd</kbd><span class="key-desc">Mark row for deletion</span></div>
    </div>
  </div>

  <h3>Data operations</h3>
  <div class="keys">
    <div>
      <div class="key-row"><kbd>s</kbd><span class="key-desc">Sort column (toggles asc / desc)</span></div>
      <div class="key-row"><kbd>H</kbd><span class="key-desc">Hide / show current column</span></div>
      <div class="key-row"><kbd>x</kbd><span class="key-desc">Column statistics</span></div>
      <div class="key-row"><kbd>f</kbd><span class="key-desc">Toggle filter bar</span></div>
    </div>
    <div>
      <div class="key-row"><kbd>/</kbd><span class="key-desc">Search across all columns</span></div>
      <div class="key-row"><kbd>n / N</kbd><span class="key-desc">Next / previous match</span></div>
      <div class="key-row"><kbd>:</kbd><span class="key-desc">SQL query prompt (DuckDB)</span></div>
      <div class="key-row"><kbd>S</kbd><span class="key-desc">View schema</span></div>
    </div>
  </div>

  <h3>File &amp; export</h3>
  <div class="keys">
    <div>
      <div class="key-row"><kbd>o / Ctrl+O</kbd><span class="key-desc">Open file browser</span></div>
      <div class="key-row"><kbd>w</kbd><span class="key-desc">Save edits to <code>_edited</code> file</span></div>
    </div>
    <div>
      <div class="key-row"><kbd>W</kbd><span class="key-desc">Export as CSV, Excel, or Parquet</span></div>
      <div class="key-row"><kbd>q</kbd><span class="key-desc">Quit (warns on unsaved edits)</span></div>
    </div>
  </div>

  <div class="note" style="margin-top: 1.5rem;">
    <strong>Save behaviour:</strong> For Parquet files, <kbd>w</kbd> writes <code>&lt;filename&gt;_edited.parquet</code>. For <code>.zst</code> files, it writes an uncompressed <code>.edited.jsonl</code> — the full file is decompressed for the save pass.
  </div>
</div>


<!-- ═══════════════════════════════════════════════════════
     HOW IT WORKS
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>How it works</h2>

  <h3>Parquet files</h3>
  <p>Loaded via <code>pyarrow</code> into a <code>pandas.DataFrame</code>. Files over 5 000 rows use lazy row-group loading — only the groups needed for the current viewport are read. The <code>ParquetFile</code> object stays open so seeking between row groups is cheap.</p>

  <h3>.jsonl.zst files — LazyJsonlReader</h3>
  <p>A custom streaming class that mirrors the Parquet lazy-loading pattern for compressed JSONL:</p>

  <ol style="color: var(--muted); font-size: 14px; margin: 1rem 0 1rem 1.5rem; line-height: 2;">
    <li><strong style="color: var(--text);">Phase 1 (instant):</strong> Opens the file, decompresses the first 100 MB, and uses the output to infer a flattened column schema (nested JSON keys joined with <code>.</code>), estimate total row count from decompressed bytes per MB, and populate the first cache window of 3 000 rows.</li>
    <li><strong style="color: var(--text);">Phase 2 (streaming):</strong> A single <code>stream_reader</code> walks the decompressed output as you scroll forward, filling a sliding 3 000-row cache. Forward scrolling extends the stream; backward jumps reopen it from the beginning.</li>
  </ol>

  <h3>Step pipeline execution</h3>
  <p>Steps are parsed from CLI specs or TUI prompts into <code>Step</code> objects, then dispatched through <code>_STEP_MAP</code> to handler functions. Each handler receives a <code>PipelineState</code> (current dataframe + metadata) and returns a <code>StepResult</code>. The TUI and batch-mode CLI share the same handlers — the only difference is whether results go to the DataTable widget or stdout.</p>

  <h3>Cell editing</h3>
  <p>Edits are held in a dictionary keyed by <code>(row, col)</code> index until save. On <kbd>w</kbd>, the full dataframe is reconstructed (reading all row groups or decompressing the full <code>.zst</code> stream), deleted rows are dropped, and edited cells are type-converted back to the original column dtype before writing.</p>
</div>


<!-- ═══════════════════════════════════════════════════════
     SUPPORTED FILE TYPES SUMMARY
═══════════════════════════════════════════════════════════ -->
<div class="section">
  <h2>Supported file types</h2>
  <table class="perf-table">
    <thead>
      <tr><th>Format</th><th>Extension</th><th>Read</th><th>Write</th><th>SQL / filter</th><th>Lazy load</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>Apache Parquet</td>
        <td><code>.parquet</code></td>
        <td style="color:var(--accent2);">✓</td>
        <td style="color:var(--accent2);">✓ <span class="perf-label">_edited.parquet</span></td>
        <td style="color:var(--accent2);">✓</td>
        <td style="color:var(--accent2);">✓ row-group</td>
      </tr>
      <tr>
        <td>Compressed JSONL</td>
        <td><code>.jsonl.zst</code></td>
        <td style="color:var(--accent2);">✓</td>
        <td style="color:var(--accent2);">✓ <span class="perf-label">.edited.jsonl</span></td>
        <td style="color:var(--muted);">full decomp</td>
        <td style="color:var(--accent2);">✓ streaming</td>
      </tr>
    </tbody>
  </table>
</div>


<!-- ═══════════════════════════════════════════════════════
     FOOTER
═══════════════════════════════════════════════════════════ -->
<div class="footer">
  <div class="footer-name">
    <strong style="color: var(--text);">Matthew Abbott</strong><br>
    <a href="mailto:mattbachg@gmail.com">mattbachg@gmail.com</a>
  </div>
  <div class="footer-license">MIT License · Copyright © 2026 Matthew Abbott</div>
</div>

</body>
</html>
