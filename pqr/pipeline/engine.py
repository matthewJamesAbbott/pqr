from __future__ import annotations

from dataclasses import dataclass, field

from pqr.commands.base import Command
from pqr.commands.impl.core_commands import FilterCmd, HideCmd, SearchCmd, SortCmd, StatsCmd
from pqr.commands.impl.io_commands import ExportCmd, SchemaCmd
from pqr.commands.impl.util_commands import DeleteCmd, PythonCmd, ShellCmd, SqlCmd, YankCmd
from pqr.pipeline.state import PipelineState, StepResult


@dataclass
class Step:
    name: str
    args: dict = field(default_factory=dict)

    @staticmethod
    def parse_spec(spec: str) -> "Step":
        if ":" in spec:
            name, rest = spec.split(":", 1)
            name = name.strip()
            rest = rest.strip()
            expr_steps = {"sql", "filter", "python", "shell", "search"}
            if name in expr_steps:
                return Step(name=name, args={"expr": rest})
            args = {}
            for part in rest.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    args[k.strip()] = v.strip()
                elif part:
                    args["expr"] = part
            return Step(name=name, args=args)
        return Step(name=spec.strip())


_COMMAND_MAP: dict[str, type[Command]] = {
    "schema": SchemaCmd,
    "yank": YankCmd,
    "search": SearchCmd,
    "filter": FilterCmd,
    "sort": SortCmd,
    "hide": HideCmd,
    "sql": SqlCmd,
    "stats": StatsCmd,
    "delete-row": DeleteCmd,
    "delete_row": DeleteCmd,
    "delrow": DeleteCmd,
    "export": ExportCmd,
    "python": PythonCmd,
    "shell": ShellCmd,
}


class Pipeline:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(self, step: Step) -> None:
        self.steps.append(step)

    def add_spec(self, spec: str) -> None:
        self.add(Step.parse_spec(spec))

    def run(self, state: PipelineState) -> list[StepResult]:
        results = []
        for step in self.steps:
            cmd_cls = _COMMAND_MAP.get(step.name)
            if cmd_cls is None:
                results.append(StepResult(
                    df=state.df,
                    message=f"[red]Unknown step: {step.name}[/red]"
                ))
                continue
            cmd = cmd_cls(step.args)
            result = cmd.execute(state)
            if result.df is not None:
                state.df = result.df
            if result.message:
                state.messages.append(result.message)
            if result.yanked:
                state.clipboard = result.yanked
            results.append(result)
        return results
