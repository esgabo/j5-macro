"""The ``exec`` step: run an external program (docs/contract.md, "Running programs").

Without ``wait`` the program is started detached and outlives the run; with it the step
waits for the program to exit, polling the run's stop signal so ``stop`` kills it (and
its children) at once, while ``pause`` lets it run and pauses the run after the step.
Nothing runs unless the run allows it (``allow_exec``), and dry runs only describe the
command. Platform details (splitting, detaching, killing) are in ``j5_macro.processes``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from j5_macro import processes
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.checks import is_interpolated
from j5_macro.steps.spec import (
    ParamProblem,
    ParamSpec,
    ResultField,
    StepContext,
    StepError,
    StepSpec,
    StepType,
    parse_duration,
)

POLL_INTERVAL = 0.05
"""Seconds between checks of the run's stop signal while a program runs."""

NOT_ALLOWED = ("running programs is not allowed in this run; allow it with "
               "j5 run --allow-exec (allow_exec in the run command)")


# --- parameters -------------------------------------------------------------------------


def _scalar_text(value: Any) -> str | None:
    """A list item or environment value as text: strings as they are, numbers written
    out, booleans as true/false; None for anything else."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return None


def _command_problem(command: Any, shell: Any) -> str | None:
    if isinstance(command, list):
        if shell is True:
            return "with shell: true the command must be a string"
        if not command:
            return "the command is empty"
        if any(_scalar_text(item) is None for item in command):
            return "every item of a command list must be a string or a number"
        return None
    if not isinstance(command, str):
        return "expected a list of strings or a command line string"
    if is_interpolated(command):
        return None
    if shell is True:
        return None if command.strip() else "the command is empty"
    try:
        processes.split_command(command)
    except ValueError as exc:
        return f"cannot split the command line: {exc}"
    return None


def check_exec(params: Mapping[str, Any]) -> list[ParamProblem]:
    """``command`` (a non-empty list, or a string that splits), ``env`` (names to
    scalars), ``cwd`` and ``timeout`` (only with ``wait``). Interpolated values are
    checked again when the step runs."""
    problems: list[ParamProblem] = []
    message = _command_problem(params.get("command"), params.get("shell"))
    if message is not None:
        problems.append(ParamProblem("command", message))
    env = params.get("env")
    if isinstance(env, Mapping):
        for name, value in env.items():
            if not isinstance(name, str) or not name or "=" in name or "\0" in name:
                problems.append(ParamProblem("env", f"invalid environment variable name "
                                             f"{name!r}"))
            elif _scalar_text(value) is None:
                problems.append(ParamProblem("env", f"the value of {name} must be a string, "
                                             "a number or a boolean"))
    if params.get("cwd") == "":
        problems.append(ParamProblem("cwd", "must not be empty"))
    if params.get("timeout") is not None and params.get("wait") is False:
        problems.append(ParamProblem("timeout", "only applies with wait: true"))
    return problems


def _command(params: Mapping[str, Any]) -> processes.Command:
    """The resolved command, as :class:`subprocess.Popen` takes it."""
    command = params["command"]
    if isinstance(command, list):
        return [text for item in command if (text := _scalar_text(item)) is not None]
    if params["shell"]:
        return str(command)
    return processes.split_command(command)


def _env(params: Mapping[str, Any]) -> dict[str, str]:
    env = params.get("env") or {}
    return {str(name): text for name, value in env.items()
            if (text := _scalar_text(value)) is not None}


def _cwd(ctx: StepContext, params: Mapping[str, Any]) -> Path:
    cwd = params.get("cwd")
    return ctx.script_dir if cwd is None else ctx.script_dir / Path(cwd).expanduser()


def _timeout(ctx: StepContext, params: Mapping[str, Any]) -> float | None:
    if ctx.timeout is not None:
        return ctx.timeout
    if params.get("timeout") is None:
        return None
    try:
        return parse_duration(params["timeout"]).seconds
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, str(exc), {"param": "timeout"}) from exc


# --- execution --------------------------------------------------------------------------


def _describe(command: processes.Command, shell: bool, cwd: Path, wait: bool,
              timeout: float | None) -> str:
    how = " through the shell" if shell else ""
    if not wait:
        then = "and go on without waiting"
    elif timeout is None:
        then = "and wait for it to exit"
    else:
        then = f"and wait up to {timeout:g}s for it to exit"
    return f"run `{processes.describe(command)}`{how} in {cwd} {then}"


def _failure(message: str, command: processes.Command, reason: str,
             **details: Any) -> StepError:
    return StepError(ErrorCode.EXEC_FAILED, message,
                     {"command": command, "reason": reason, **details})


def run_exec(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    try:
        command = _command(params)
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, f"parameter 'command': {exc}",
                        {"param": "command"}) from exc
    shell, wait = bool(params["shell"]), bool(params["wait"])
    cwd, env = _cwd(ctx, params), processes.environment(_env(params))
    timeout = _timeout(ctx, params) if wait else None
    if ctx.dry_run:
        return _dry_run(ctx, command, shell=shell, cwd=cwd, wait=wait, timeout=timeout)
    if not ctx.allow_exec:
        raise StepError(ErrorCode.CAPABILITY_MISSING, NOT_ALLOWED, {"capability": "exec"})
    if not wait:
        try:
            pid = processes.start_detached(command, shell=shell, cwd=str(cwd), env=env)
        except OSError as exc:
            raise _failure(f"cannot start {processes.describe(command)}: {exc}", command,
                           "start") from exc
        return {"pid": pid}
    return _wait_for(ctx, command, shell=shell, cwd=cwd, env=env, timeout=timeout,
                     check=bool(params["check"]))


def _dry_run(ctx: StepContext, command: processes.Command, *, shell: bool, cwd: Path,
             wait: bool, timeout: float | None) -> dict[str, Any]:
    """Describe the command without running it. The result stands in for the output
    (empty, exit code 0; no pid), so later steps that use it still validate. A strict
    dry run fails, as the real run would, when the run does not allow ``exec``."""
    if not ctx.allow_exec and ctx.strict:
        raise StepError(ErrorCode.CAPABILITY_MISSING, NOT_ALLOWED, {"capability": "exec"})
    description = f"would {_describe(command, shell, cwd, wait, timeout)}"
    ctx.log("info", description)
    if not ctx.allow_exec:
        ctx.log("warning", f"dry-run: {NOT_ALLOWED}")
    output: dict[str, Any] = ({"exit_code": 0, "stdout": "", "stderr": ""} if wait
                              else {"pid": None})
    return {"dry_run": True, "description": description, **output}


def _wait_for(ctx: StepContext, command: processes.Command, *, shell: bool, cwd: Path,
              env: dict[str, str] | None, timeout: float | None,
              check: bool) -> dict[str, Any]:
    try:
        program = processes.Waited(command, shell=shell, cwd=str(cwd), env=env)
    except OSError as exc:
        raise _failure(f"cannot start {processes.describe(command)}: {exc}", command,
                       "start") from exc
    deadline = None if timeout is None else time.monotonic() + timeout
    finished = False
    try:
        # Only stop interrupts the wait: a pause takes effect after the step.
        while not program.poll(POLL_INTERVAL):
            ctx.check_stop()
            if deadline is not None and time.monotonic() >= deadline:
                raise _failure(f"{processes.describe(command)} did not finish in "
                               f"{timeout:g}s and was killed", command, "timeout",
                               timeout=timeout)
        finished = True
    finally:
        if not finished:
            program.kill()
    done = program.result()
    result: dict[str, Any] = {"exit_code": done.exit_code, "stdout": done.stdout,
                              "stderr": done.stderr}
    if done.truncated:
        result["truncated"] = True
    if check and done.exit_code != 0:
        last = done.stderr.strip().splitlines()[-1:] or [""]
        reason = f": {last[0]}" if last[0] else ""
        raise _failure(f"{processes.describe(command)} exited with code {done.exit_code}"
                       f"{reason}", command, "exit_code", **result)
    return result


EXEC = StepType(StepSpec(
    name="exec",
    doc="Run a program: start it and go on, or wait for it and get its output. Needs the "
        "run's allow_exec (j5 run --allow-exec).",
    shorthand="command",
    params=(
        ParamSpec(name="command", type="any", required=True,
                  doc="The program and its arguments: a list (run without a shell), or a "
                      "command line string (split like one, still without a shell)."),
        ParamSpec(name="shell", type="boolean", default=False,
                  doc="Run the command string through the system shell (sh -c, cmd /c)."),
        ParamSpec(name="wait", type="boolean", default=False,
                  doc="Wait for the program to exit and return exit_code, stdout and "
                      "stderr; otherwise start it detached and return its pid."),
        ParamSpec(name="timeout", type="duration",
                  doc="With wait: kill the program after this long (exec_failed)."),
        ParamSpec(name="cwd", type="string",
                  doc="Working directory, relative to the script's directory (the "
                      "default)."),
        ParamSpec(name="env", type="object",
                  doc="Extra environment variables; pass secrets here rather than in "
                      "the command."),
        ParamSpec(name="check", type="boolean", default=True,
                  doc="With wait: fail the step (exec_failed) when the exit code is not "
                      "0."),
    ),
    result=(
        ResultField(name="exit_code", type="integer", optional=True,
                    doc="With wait: the program's exit code."),
        ResultField(name="stdout", type="string", optional=True,
                    doc="With wait: the program's output as text, final line break "
                        "included (use strip())."),
        ResultField(name="stderr", type="string", optional=True,
                    doc="With wait: the program's error output as text."),
        ResultField(name="truncated", type="boolean", optional=True,
                    doc="With wait: true when stdout or stderr was cut at 64 KiB."),
        ResultField(name="pid", type="integer", optional=True,
                    doc="Without wait: the process id of the program started (null in "
                        "dry runs)."),
        ResultField(name="dry_run", type="boolean", optional=True,
                    doc="True in dry runs, whose result adds a description to stand-in "
                        "output: exit_code 0 and empty stdout and stderr with wait, a "
                        "null pid without."),
        ResultField(name="description", type="string", optional=True,
                    doc="Dry runs: what the step would run."),
    ),
), run_exec)
