"""The worker side of a run: an explicit program counter over a frame stack.

There is no recursion over the step tree. The main loop looks at the top frame: if it
has a step left, that step passes a *checkpoint* (where pauses, breakpoints and stepping
take effect) and runs; an action step advances the frame's index, a block step pushes a
frame for its block. When a frame runs out of steps, a loop starts its next iteration
or the frame is popped and its block step finishes. Because the whole position lives in
the stack, the run can stop at any step, at any depth, and resume from there.

Stepping is expressed with frame depths (the root frame is depth 1). After a command at
a step of depth ``d``: ``step_over`` pauses at the next checkpoint of depth ``<= d``,
``step_into`` at the next checkpoint, ``step_out`` at the next one of depth ``< d``.
"""

from __future__ import annotations

import random
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, TypeVar

from j5_macro.expr import ExpressionError
from j5_macro.input.api import EmergencyStop, StepInput, UserInterference
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads.events import StateReason
from j5_macro.runtime.api import RunOptions
from j5_macro.runtime.clock import Clock
from j5_macro.runtime.context import RunContext
from j5_macro.runtime.control import Command, RunControl, RunStopped
from j5_macro.runtime.emitter import Emitter, Iterations
from j5_macro.runtime.failures import RunFailed, at_step, internal_error, step_exception
from j5_macro.runtime.frames import Frame, LoopState, iterations
from j5_macro.runtime.input import RunInput
from j5_macro.runtime.resolve import ConditionFn, check_condition, evaluated, resolve_params
from j5_macro.runtime.vision import BoundVision, RunVision
from j5_macro.script.model import Script, Step
from j5_macro.script.params import secret_texts
from j5_macro.steps.registry import StepRegistry
from j5_macro.steps.spec import LogLevel, StepError, StepSpec
from j5_macro.vision.api import StepVision

MAX_ATTEMPTS = 3
"""Attempts in total for ``on_error: retry``."""
DRY_RUN_WHILE_CAP = 1000
"""Iterations after which a dry run ends a ``while`` loop."""

T = TypeVar("T")


class Machine:
    """Executes one script on the worker thread; see the module docstring."""

    def __init__(self, script: Script, registry: StepRegistry, events: Emitter,
                 control: RunControl, clock: Clock, options: RunOptions,
                 conditions: Mapping[str, ConditionFn], run_input: RunInput,
                 vision: RunVision) -> None:
        self._script = script
        self._input = run_input
        self._vision = vision
        self._registry = registry
        self._events = events
        self._control = control
        self._clock = clock
        self._seed = options.seed
        self._dry_run = options.mode == "dry"
        self._allow_exec = options.allow_exec
        self._script_dir = Path(script.source_path).parent if script.source_path else None
        self._conditions = conditions
        self._add_secrets = options.add_secrets
        self._vars: dict[str, Any] = {**script.vars, **options.variables}
        self._stack: list[Frame] = []
        self.position: Step | None = None
        """The step being run or paused at; read under the control lock."""
        self.position_iterations: Iterations = None
        """``iterations`` of :attr:`position` (as in its step events); same lock."""
        self._initial_pause = options.mode == "step"
        self._target: int | None = None
        """Stepping target depth: pause at the next checkpoint of at most this depth."""
        self._paused = 0.0
        """Seconds spent paused so far, excluded from durations."""

    # --- ContextHost ----------------------------------------------------------------

    @property
    def variables(self) -> dict[str, Any]:
        """The live variables; write them only through :meth:`assign`."""
        return self._vars

    def assign(self, name: str, value: Any, step_id: str | None) -> None:
        with self._control.lock:
            old = self._vars.get(name)
            self._vars[name] = value
        self._events.variable_changed(name, old, value, step_id)

    def log(self, level: LogLevel, message: str, step_id: str | None) -> None:
        self._events.log(level, message, step_id)

    def check_stop(self) -> None:
        self._control.check_stop()

    def step_input(self, step_id: str, rng: random.Random,
                   *overrides: Mapping[str, Any] | None) -> StepInput:
        return self._input.bind(step_id, rng, *overrides)

    def step_vision(self, context: RunContext) -> StepVision:
        return BoundVision(self._vision, step_id=context.step_id, step_timeout=context.timeout,
                           wait=context.wait, check_stop=context.check_stop, log=context.log,
                           paused=lambda: self._paused)

    def wait(self, seconds: float, depth: int) -> None:
        remaining = seconds
        while remaining > 0:
            remaining = self._control.sleep(remaining)
            if remaining > 0:
                self._pause("user", depth)

    # --- main loop ------------------------------------------------------------------

    def execute(self) -> None:
        """Run the script to the end; raises RunStopped or RunFailed."""
        self._stack = [Frame(steps=list(self._script.steps))]
        while self._stack:
            self._control.check_stop()
            frame = self._stack[-1]
            if frame.index >= len(frame.steps):
                self._end_of_block()
                continue
            step = frame.steps[frame.index]
            self._checkpoint(step)
            if step.type == "if":
                self._enter_if(step)
            elif step.type == "loop":
                self._enter_loop(step)
            else:
                self._run_action(step)

    def _checkpoint(self, step: Step) -> None:
        depth = len(self._stack)
        its = iterations(self._stack)
        self._set_position(step, its)
        reason: StateReason
        if self._initial_pause:
            self._initial_pause = False
            reason = "step"
        elif step.breakpoint or self._control.is_breakpoint(step.id):
            self._events.breakpoint_hit(step, its)
            reason = "breakpoint"
        elif self._control.pause_pending():
            reason = "user"
        elif self._target is not None and depth <= self._target:
            reason = "step"
        else:
            return
        self._pause(reason, depth)

    def first_step(self) -> Step | None:
        """The script's first top-level step, None for an empty script."""
        return self._script.steps[0] if self._script.steps else None

    def place_at(self, step: Step) -> None:
        """Record ``step`` (top level) as the current position before the run starts."""
        self._set_position(step, None)

    def _set_position(self, step: Step, its: Iterations) -> None:
        with self._control.lock:
            self.position = step
            self.position_iterations = its

    def _pause(self, reason: StateReason, depth: int) -> Command:
        """Pause at depth ``depth`` until a command; sets the stepping target."""
        if not self._control.enter_pause():
            self._events.state("paused", reason)
        paused_at = self._clock.now()
        command = self._control.wait_command()
        self._paused += self._clock.now() - paused_at
        self._target = {
            "resume": None,
            "step_over": depth,
            "step_into": sys.maxsize,
            "step_out": depth - 1,
        }[command]
        self._events.state("running", "user" if command == "resume" else "step")
        return command

    # --- failures -------------------------------------------------------------------

    def _guarded(self, step: Step, its: Iterations, depth: int,
                 action: Callable[[int], T]) -> T | None:
        """Run ``action(attempt)`` applying ``step.on_error``; None if the step is skipped."""
        attempt = 1
        while True:
            screenshot: str | None = None
            try:
                return action(attempt)
            except StepError as exc:
                info, screenshot = exc.info, exc.screenshot
            except ExpressionError as exc:
                info = exc.info
            except EmergencyStop as exc:
                # The master already released what it held (corner failsafe).
                self._events.log("warning", f"emergency stop: {exc.reason}", step.id)
                self._control.stop("user")
                raise RunStopped("user") from exc
            except UserInterference as exc:
                # The master already released what the action held and emitted
                # user_interference. The interrupted attempt neither finishes nor fails.
                if self._interfered(step, its, depth, exc):
                    return None
                attempt += 1
                continue
            except Exception as exc:
                info = step_exception(exc)
            info = at_step(info, step, attempt)
            self._set_position(step, its)
            self._events.step_failed(step, its, info, screenshot)
            if step.on_error == "continue":
                self._events.log("warning", f"step '{step.id}' failed, continuing: "
                                 f"{info.message}", step.id)
                return None
            if step.on_error == "retry" and attempt < MAX_ATTEMPTS:
                attempt += 1
                continue
            if step.on_error == "pause":
                if self._pause("error", depth) == "step_over":
                    self._events.step_skipped(step, its, "user")
                    return None
                attempt += 1
                continue
            raise RunFailed(info)

    def _interfered(self, step: Step, its: Iterations, depth: int,
                    exc: UserInterference) -> bool:
        """Apply the profile's interference action: stop the run (raises RunStopped) or
        pause; True if the user then skipped the step, False to run it again."""
        if exc.action == "stop":
            self._events.log("warning", f"user interference: {exc}; stopping the run",
                             step.id)
            self._control.stop("interference")
            raise RunStopped("interference") from exc
        self._set_position(step, its)
        if self._pause("interference", depth) == "step_over":
            self._events.step_skipped(step, its, "user")
            return True
        return False

    # --- action steps ---------------------------------------------------------------

    def _run_action(self, step: Step) -> None:
        depth = len(self._stack)
        its = iterations(self._stack)

        def attempt(number: int) -> RunContext:
            started, paused = self._clock.now(), self._paused
            step_type = self._registry.get(step.type)
            if step_type is None or step_type.executor is None:
                self._events.step_started(step, its, dict(step.params))
                raise StepError(ErrorCode.UNKNOWN_STEP_TYPE,
                                f"no executor for step type {step.type!r}")
            params = self._resolve_and_start(step, its, step_type.spec)
            ctx = self._context(step, its, number, depth)
            result = step_type.executor(ctx, params)
            self._step_finished(step, its, self._elapsed_ms(started, paused), result)
            return ctx

        ctx = self._guarded(step, its, depth, attempt)
        self._stack[-1].index += 1
        if ctx is not None and ctx.pause_requested:
            self._pause("breakpoint", depth)

    def _step_finished(self, step: Step, its: Iterations, duration_ms: int,
                       result: dict[str, Any] | None) -> None:
        """Emit ``step_finished`` and keep the result in the step's ``as`` variable: a
        secret result is registered with the engine first, so its own ``step_finished``
        is masked too."""
        store = step.store_as
        if store is not None and store.secret and self._add_secrets is not None:
            texts = secret_texts(result)
            if texts:
                self._add_secrets(texts)
        self._events.step_finished(step, its, duration_ms, result)
        if store is not None:
            self.assign(store.name, result, step.id)

    def _resolve_and_start(self, step: Step, its: Iterations, spec: StepSpec) -> dict[str, Any]:
        """Resolve the params and emit ``step_started`` (with the raw params on failure)."""
        try:
            params = resolve_params(self._registry, spec, step.params, self._vars)
        except StepError:
            self._events.step_started(step, its, dict(step.params))
            raise
        self._events.step_started(step, its, params)
        return params

    def _context(self, step: Step, its: Iterations, attempt: int, depth: int) -> RunContext:
        return RunContext(self, step_id=step.id, iterations=its, attempt=attempt, depth=depth,
                          dry_run=self._dry_run, seed=self._seed, input_overrides=step.input,
                          timeout=step.timeout, script_dir=self._script_dir,
                          allow_exec=self._allow_exec)

    def _elapsed_ms(self, started: float, paused_before: float) -> int:
        active = self._clock.now() - started - (self._paused - paused_before)
        return max(0, round(active * 1000))

    # --- blocks ---------------------------------------------------------------------

    def _enter_if(self, step: Step) -> None:
        depth = len(self._stack)
        its = iterations(self._stack)
        started, paused = self._clock.now(), self._paused
        condition = step.params.get("when")

        def attempt(number: int) -> str:
            self._events.step_started(step, its, dict(step.params))
            ctx = self._context(step, its, number, depth)
            value = check_condition(condition, ctx, self._conditions)
            branch: Literal["then", "else"] = "then" if value else "else"
            self._events.branch_taken(step, its, condition, value, branch)
            return branch

        branch = self._guarded(step, its, depth, attempt)
        if branch is None:
            self._stack[-1].index += 1
            return
        self._push(Frame(steps=step.blocks.get(branch, []), owner=step, started=started,
                         paused_before=paused, result={"branch": branch}))

    def _enter_loop(self, step: Step) -> None:
        depth = len(self._stack)
        its = iterations(self._stack)
        started, paused = self._clock.now(), self._paused

        def attempt(number: int) -> tuple[LoopState, bool]:
            spec = self._registry.spec(step.type)
            if spec is None:
                raise StepError(ErrorCode.UNKNOWN_STEP_TYPE, "the 'loop' step is not registered")
            params = self._resolve_and_start(step, its, spec)
            ctx = self._context(step, its, number, depth)
            state = self._loop_state(step, params)
            return state, self._loop_continues(state, ctx)

        entered = self._guarded(step, its, depth, attempt)
        if entered is None:
            self._stack[-1].index += 1
            return
        state, go = entered
        frame = Frame(steps=step.blocks.get("do", []), owner=step, loop=state, started=started,
                      paused_before=paused, result={"iterations": 0})
        if not go:
            self._finish(frame)
            return
        self._push(frame)
        self._begin_iteration(frame)

    def _loop_state(self, step: Step, params: dict[str, Any]) -> LoopState:
        if "times" in params:
            return LoopState(total=params["times"])
        if "for_each" not in params:
            return LoopState(condition=params.get("while"))
        items = params["for_each"]
        raw = step.params["for_each"]
        if isinstance(raw, str) and "${" not in raw:
            items = evaluated(raw, self._vars)
        if isinstance(items, tuple):
            items = list(items)
        if not isinstance(items, list):
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            f"'for_each' must be a list, got {type(items).__name__}",
                            {"param": "for_each"})
        return LoopState(total=len(items), items=items, var=params.get("as") or "item")

    def _loop_continues(self, state: LoopState, ctx: RunContext) -> bool:
        if state.total is not None:
            return state.index < state.total
        if self._dry_run and state.index >= DRY_RUN_WHILE_CAP:
            ctx.log("warning", f"dry-run: 'while' loop stopped after {DRY_RUN_WHILE_CAP} "
                    "iterations")
            return False
        return check_condition(state.condition, ctx, self._conditions)

    def _begin_iteration(self, frame: Frame) -> None:
        state, owner = frame.loop, frame.owner
        assert state is not None and owner is not None
        frame.index = 0
        frame.result = {"iterations": state.index + 1}
        self._events.loop_iteration(owner, iterations(self._stack[:-1]), state.index,
                                    state.total)
        if state.items is not None and state.var is not None:
            self.assign(state.var, state.items[state.index], owner.id)

    def _push(self, frame: Frame) -> None:
        """Enter a block, or finish its step at once if the block is empty."""
        if frame.steps:
            self._stack.append(frame)
        else:
            self._finish(frame)

    def _finish(self, frame: Frame) -> None:
        """Emit ``step_finished`` for ``frame``'s block step and move past it."""
        assert frame.owner is not None
        self._step_finished(frame.owner, iterations(self._stack),
                            self._elapsed_ms(frame.started, frame.paused_before),
                            frame.result)
        self._stack[-1].index += 1

    def _end_of_block(self) -> None:
        frame = self._stack[-1]
        owner, state = frame.owner, frame.loop
        if owner is None:
            self._stack.pop()
            return
        if state is not None:
            state.index += 1
            depth = len(self._stack) - 1
            its = iterations(self._stack[:-1])
            go = self._guarded(owner, its, depth, lambda number: self._loop_continues(
                state, self._context(owner, its, number, depth)))
            if go:
                self._begin_iteration(frame)
                return
            if go is None:
                self._stack.pop()
                self._stack[-1].index += 1
                return
        self._stack.pop()
        self._finish(frame)


def failure_info(exc: BaseException) -> ErrorInfo:
    """The outcome error for an exception that escaped the machine."""
    if isinstance(exc, RunFailed):
        return exc.info
    return internal_error(exc)
