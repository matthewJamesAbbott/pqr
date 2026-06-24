from __future__ import annotations

import sys
from pathlib import Path

from pqr.common import json, re, _add_to_history, _load_history, _save_history, _is_zst_file
from pqr.io.reader import JsonlReader, ReaderFactory
from pqr.pipeline.engine import Pipeline, Step
from pqr.pipeline.state import PipelineState


class PQRSTask:
    """High-level orchestrator: open(), run(), save()."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._pipeline = Pipeline()

    @staticmethod
    def build_state(path: str) -> PipelineState:
        """Build PipelineState using ReaderFactory (Strategy pattern)."""
        if _is_zst_file(path):
            return _build_state_zst(path)
        from pqr.common import pq
        reader = ReaderFactory.create(path)
        df = pq.read_table(path).to_pandas()
        pq_file = pq.ParquetFile(path)
        return PipelineState(
            df=df,
            schema=pq_file.schema_arrow,
            path=Path(path),
            _reader=reader,
        )

    def add_step(self, spec: str) -> None:
        self._pipeline.add_spec(spec)

    def run(self) -> list:
        state = self.build_state(self._path)
        return self._pipeline.run(state)

    @staticmethod
    def build_pipeline_from_args(args) -> Pipeline:
        pipeline = Pipeline()
        if args.steps:
            for spec in args.steps.split(","):
                spec = spec.strip()
                if spec:
                    pipeline.add_spec(spec)
        for spec in args.step:
            pipeline.add_spec(spec)
        if args.sql:
            pipeline.add_spec(f"sql:{args.sql}")
        if args.filter:
            pipeline.add_spec(f"filter:{args.filter}")
        if args.sort:
            pipeline.add_spec(f"sort:column={args.sort}")
        if args.yank:
            row_arg = f";row={args.row}" if args.row else ""
            pipeline.add_spec(f"yank:column={args.yank}{row_arg}")
        if args.schema:
            pipeline.add_spec("schema")
        if args.export:
            pipeline.add_spec(f"export:format={args.format};output={args.output}")
        if args.shortcut:
            shortcuts = _load_shortcuts()
            if args.shortcut not in shortcuts:
                print(f"Error: shortcut '{args.shortcut}' not found", file=sys.stderr)
                print(f"Available shortcuts: {', '.join(shortcuts.keys()) if shortcuts else 'none'}", file=sys.stderr)
                sys.exit(1)
            sc = shortcuts[args.shortcut]
            for spec in sc.get("steps", []):
                pipeline.add_spec(spec)
        return pipeline


def _build_state_zst(path: str) -> PipelineState:
    reader = JsonlReader(path)
    reader._ensure_indexed()
    initial = reader.get_row_range(0, min(reader.CHUNK_ROWS, reader.num_rows))
    return PipelineState(
        df=initial,
        schema=None,
        path=Path(path),
        _reader=reader,
    )


def _load_shortcuts() -> dict:
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
