from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pqr.io.reader import Reader

from pqr.common import pd, pa


@dataclass
class StepResult:
    df: "pd.DataFrame | None" = None
    message: str = ""
    yanked: str = ""
    output: Any = None


@dataclass
class PipelineState:
    df: "pd.DataFrame | None" = None
    schema: "pa.Schema | None" = None
    path: Path | None = None
    hidden_cols: set = field(default_factory=set)
    sort_col: str | None = None
    sort_asc: bool = True
    clipboard: str = ""
    messages: list[str] = field(default_factory=list)
    args: dict = field(default_factory=dict)
    _reader: "Reader | None" = None
