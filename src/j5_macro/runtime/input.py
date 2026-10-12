"""The run's input: one :class:`~j5_macro.input.api.InputSession` per run, and the
:class:`~j5_macro.input.api.StepInput` each step sees as ``ctx.input``.

docs/contract.md, "Mouse and keyboard masters". The session is created lazily, the
first time a step asks for input, with a :data:`~j5_macro.input.api.SessionFactory`
(by default ``j5_macro.input.session.create_session``). Its events go out through the
run's event sink, and it notices ``stop`` through :class:`RunAbort`. Whatever it still
holds is released when the run ends.
"""

from __future__ import annotations

import importlib
import random
import threading
from collections.abc import Mapping
from typing import Any, cast

from j5_macro.backends.base import Backend, Button, Rect
from j5_macro.input.api import InputProfile, InputSession, SessionFactory, StepInput
from j5_macro.protocol.errors import ErrorCode
from j5_macro.runtime.api import RunEventSink
from j5_macro.runtime.control import RunControl
from j5_macro.steps.spec import StepError

SESSION_MODULE = "j5_macro.input.session"
"""Provides ``create_session``, the default :data:`SessionFactory`."""
RELEASE_REASON = "run_ended"


class RunAbort:
    """The :class:`~j5_macro.input.api.AbortSignal` of a run.

    ``check`` and ``sleep`` raise the interpreter's ``RunStopped`` on ``stop``. A pause
    request never interrupts them: masters finish the action and the run pauses at the
    next checkpoint.
    """

    def __init__(self, control: RunControl) -> None:
        self._control = control

    def check(self) -> None:
        self._control.check_stop()

    def sleep(self, seconds: float) -> None:
        self._control.sleep_unless_stopped(seconds)


def default_session_factory() -> SessionFactory:
    """``j5_macro.input.session.create_session``, imported when first needed.

    Raises ``StepError(capability_missing)`` when the input layer is not available.
    """
    try:
        module = importlib.import_module(SESSION_MODULE)
    except ImportError as exc:
        raise StepError(ErrorCode.CAPABILITY_MISSING,
                        f"the input session is not available ({SESSION_MODULE}): {exc}") from exc
    factory = getattr(module, "create_session", None)
    if factory is None:
        raise StepError(ErrorCode.CAPABILITY_MISSING,
                        f"{SESSION_MODULE} has no 'create_session'")
    return cast(SessionFactory, factory)


def effective_profile(*layers: Mapping[str, Any] | None) -> InputProfile:
    """``InputProfile()`` with each override applied in order (script ``input``, run
    ``input_profile``, step ``input``, step parameters); ``validation_failed`` if one is
    invalid."""
    profile = InputProfile()
    for layer in layers:
        try:
            profile = profile.merged(layer)
        except (TypeError, ValueError) as exc:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            f"invalid input profile: {exc}") from exc
    return profile


class RunInput:
    """Owns the run's input session (see the module docstring)."""

    def __init__(self, *, backend: Backend | None, reason: str | None, dry_run: bool,
                 emit: RunEventSink, control: RunControl,
                 factory: SessionFactory | None,
                 base: tuple[Mapping[str, Any] | None, ...]) -> None:
        self.backend = backend
        """The engine's backend; a dry run never creates a session on it."""
        self._reason = reason
        self._dry_run = dry_run
        self._emit = emit
        self._control = control
        self._factory = factory
        self._base = base
        self._session: InputSession | None = None
        self._lock = threading.Lock()

    @property
    def session(self) -> InputSession | None:
        """The session, if a step has created it."""
        with self._lock:
            return self._session

    def _ensure_session(self, step_id: str) -> tuple[Backend, InputSession]:
        if self._dry_run:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"step '{step_id}' asked for input in a dry run")
        backend = self.backend
        if backend is None:
            why = f": {self._reason}" if self._reason else ""
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"step '{step_id}' needs an input backend{why}")
        with self._lock:
            if self._session is None:
                factory = self._factory if self._factory is not None \
                    else default_session_factory()
                self._session = factory(backend, self._emit, RunAbort(self._control))
            return backend, self._session

    def bind(self, step_id: str, rng: random.Random,
             *overrides: Mapping[str, Any] | None) -> StepInput:
        """``ctx.input`` for one step attempt: the session, the step's ``rng`` and the
        effective profile (the run's layers, then ``overrides``)."""
        backend, session = self._ensure_session(step_id)
        profile = effective_profile(*self._base, *overrides)
        return BoundInput(session, backend, rng, profile)

    def release(self) -> list[str]:
        """Release everything the session still holds (no-op without a session)."""
        session = self.session
        if session is None:
            return []
        return session.release_all(RELEASE_REASON)


class BoundInput:
    """A :class:`~j5_macro.input.api.StepInput`: the session bound to one step."""

    def __init__(self, session: InputSession, backend: Backend, rng: random.Random,
                 profile: InputProfile) -> None:
        self._session = session
        self._backend = backend
        self._rng = rng
        self._profile = profile

    @property
    def profile(self) -> InputProfile:
        return self._profile

    def _require_pointer(self) -> None:
        """``capability_missing`` when interference detection is on (the profile's
        ``interference`` is not ``off``) and the backend cannot report the pointer."""
        mouse = self._profile.mouse
        if mouse.interference != "off" and not self._backend.capabilities.pointer_position:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"the {self._backend.name} backend cannot report the pointer "
                            "position, needed to detect user interference; set the mouse "
                            "profile's 'interference' to 'off' to move without it",
                            {"capability": "pointer_position"})

    def move(self, x: int, y: int, *, target: Rect | None = None) -> None:
        self._require_pointer()
        self._session.move(x, y, profile=self._profile.mouse, rng=self._rng, target=target)

    def click(self, x: int, y: int, *, button: Button = Button.LEFT, clicks: int = 1,
              target: Rect | None = None) -> None:
        self._require_pointer()
        self._session.click(x, y, button=button, clicks=clicks, profile=self._profile.mouse,
                            rng=self._rng, target=target)

    def drag(self, start: tuple[int, int], end: tuple[int, int], *,
             button: Button = Button.LEFT) -> None:
        self._require_pointer()
        self._session.drag(start, end, button=button, profile=self._profile.mouse,
                           rng=self._rng)

    def scroll(self, dx: int, dy: int) -> None:
        self._session.scroll(dx, dy, profile=self._profile.mouse, rng=self._rng)

    def type_text(self, text: str) -> None:
        self._session.type_text(text, profile=self._profile.keyboard, rng=self._rng)

    def press(self, combo: str, *, hold: float | None = None) -> None:
        self._session.press(combo, hold=hold, profile=self._profile.keyboard, rng=self._rng)

    def find_window(self, title: str) -> Rect | None:
        """``capability_missing`` when the backend cannot find windows."""
        if not self._backend.capabilities.windows:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"the {self._backend.name} backend cannot find windows "
                            "(window-relative coordinates)")
        return self._session.find_window(title)
