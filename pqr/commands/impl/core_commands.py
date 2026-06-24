from __future__ import annotations

from pqr.commands.base import Command
from pqr.common import _is_container, pd
from pqr.pipeline.state import PipelineState, StepResult


class SearchCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        pattern = self.args.get("expr", "").lower()
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
        detail = "\n".join(f"  row {r}, col {c}" for r, c in matches[:50])
        if len(matches) > 50:
            detail += f"\n  ... and {len(matches)-50} more"
        return StepResult(df=state.df, message=f"Found {len(matches)} matches:\n{detail}")


class FilterCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        expr = self.args.get("expr", "")
        if not expr or state.df is None:
            return StepResult(df=state.df, message="No filter expression")
        df = state.df
        filtered = df.query(expr)
        return StepResult(df=filtered, message=f"Filtered: {len(filtered)} / {len(df)} rows")


class SortCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        col = self.args.get("column") or self.args.get("expr")
        desc = self.args.get("desc", "false").lower() == "true"
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
        return StepResult(df=df, message=f"Sorted by {col} {'desc' if desc else 'asc'}")


class HideCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        col = self.args.get("column") or self.args.get("expr")
        if not col or state.df is None:
            return StepResult(df=state.df, message="Hide requires --column")
        state.hidden_cols.add(col)
        return StepResult(df=state.df, message=f"Hidden column: {col}")


class StatsCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        col = self.args.get("column")
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
