from __future__ import annotations

from pqr.commands.base import Command
from pqr.common import base64, os, pd, subprocess
from pqr.pipeline.state import PipelineState, StepResult


def _try_copy(text: str) -> bool:
    def _try_cmd(cmd: list[str]) -> bool:
        try:
            subprocess.run(cmd, input=text.encode(), capture_output=True, check=True)
            return True
        except Exception:
            return False

    def _osc52(device: str) -> bool:
        try:
            b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
            osc = f"\x1b]52;c;{b64}\x07"
            with open(device, "w") as tty:
                tty.write(osc)
                tty.flush()
            return True
        except Exception:
            return False

    if os.environ.get("DISPLAY") and _try_cmd(["xclip", "-selection", "clipboard"]):
        return True
    if os.environ.get("WAYLAND_DISPLAY") and _try_cmd(["wl-copy"]):
        return True
    if _try_cmd(["pbcopy"]):
        return True
    if _try_cmd(["clip"]):
        return True
    tty_candidates = []
    if os.environ.get("TMUX"):
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{client_tty}"],
                capture_output=True, text=True
            )
            client_tty = result.stdout.strip()
            if client_tty.startswith("/dev/"):
                tty_candidates.append(client_tty)
        except Exception:
            pass
    if os.environ.get("SSH_TTY"):
        tty_candidates.append(os.environ["SSH_TTY"])
    tty_candidates.append("/dev/tty")
    try:
        own_fd = os.readlink("/proc/self/fd/0")
        if own_fd.startswith("/dev/"):
            tty_candidates.append(own_fd)
    except Exception:
        pass
    seen = set()
    for dev in tty_candidates:
        if dev not in seen:
            seen.add(dev)
            if _osc52(dev):
                return True
    return False


class YankCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        col = self.args.get("column") or self.args.get("expr")
        row = self.args.get("row")
        if col is None:
            return StepResult(df=state.df, message="Yank requires --column or --row")
        df = state.df
        if df is None or col not in df.columns:
            return StepResult(df=state.df, message=f"Column '{col}' not found")
        if row is not None:
            row = int(row)
            if row < 0:
                row = len(df) + row
            val = df[col].iloc[row]
        else:
            val = df[col].to_list()
        text = str(val)
        _try_copy(text)
        state.clipboard = text
        return StepResult(df=state.df, message=f"Yanked: {text[:120]}", yanked=text)


class DeleteCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        row = self.args.get("row", "0")
        row = int(row)
        if state.df is None or len(state.df) == 0:
            return StepResult(df=state.df, message="No data")
        if row < 0:
            row = len(state.df) + row
        if row >= len(state.df) or row < 0:
            return StepResult(df=state.df, message=f"Row index {row} out of range")
        df = state.df.drop(index=row).reset_index(drop=True)
        return StepResult(df=df, message=f"Deleted row {row}")


class SqlCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        expr = self.args.get("expr", "")
        if not expr or state.df is None:
            return StepResult(df=state.df, message="No SQL expression")
        try:
            import duckdb
            con = duckdb.connect()
            con.register("df", state.df)
            result = con.execute(expr).fetchdf()
            return StepResult(df=result, message=f"SQL result: {len(result)} rows")
        except ImportError:
            return StepResult(df=state.df, message="Install duckdb: pip install duckdb")
        except Exception as e:
            return StepResult(df=state.df, message=f"SQL error: {e}")


class PythonCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        expr = self.args.get("expr", "")
        if not expr or state.df is None:
            return StepResult(df=state.df, message="No Python expression")
        local_ns = {"df": state.df, "pd": pd}
        try:
            result = eval(expr, {"__builtins__": __builtins__}, local_ns)
            if isinstance(result, pd.DataFrame):
                return StepResult(df=result, message=f"Python result: {len(result)} rows")
            return StepResult(df=state.df, message=f"Python: {result}")
        except Exception as e:
            return StepResult(df=state.df, message=f"Python error: {e}")


class ShellCmd(Command):
    def __init__(self, args: dict) -> None:
        self.args = args

    def execute(self, state: PipelineState) -> StepResult:
        cmd = self.args.get("expr", "")
        if not cmd or state.df is None:
            return StepResult(df=state.df, message="No shell command")
        try:
            csv_data = state.df.to_csv(index=False)
            proc = subprocess.run(
                cmd, shell=True, input=csv_data,
                capture_output=True, check=True, text=True
            )
            return StepResult(df=state.df, message=proc.stdout)
        except subprocess.CalledProcessError as e:
            return StepResult(df=state.df, message=f"Shell error: {e.stderr}")
        except Exception as e:
            return StepResult(df=state.df, message=f"Shell error: {e}")
