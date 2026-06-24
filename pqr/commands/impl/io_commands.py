from __future__ import annotations

from pqr.commands.base import Command
from pqr.common import pd, pq, _is_container
from pqr.io.reader import JsonlReader
from pqr.pipeline.state import PipelineState, StepResult


class SchemaCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        from pathlib import Path
        buf = f"File: {state.path}\n"
        reader = state._reader
        if isinstance(reader, JsonlReader):
            buf += f"Columns: {len(reader.columns)}\n"
            buf += f"Rows: {reader.num_rows}\n\n"
            buf += "Column Details:\n"
            for i, col in enumerate(reader.columns):
                buf += f"  {i+1}. {col} : {reader.dtypes.get(col, 'object')}\n"
            return StepResult(df=state.df, message=buf)
        if state.schema is None:
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


class ExportCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        fmt = self.args.get("format", "csv")
        out = self.args.get("output", "-")
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
