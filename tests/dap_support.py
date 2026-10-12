"""Shared helpers for the DAP adapter tests: a script tree and a fake engine."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from j5_macro.engine.scripts import step_node
from j5_macro.protocol import Event
from j5_macro.script.loader import load_script
from j5_macro.steps.builtins import default_registry

SCRIPT = """\
version: 1
name: Unit
vars:
  n: 0
  data: {list: [1, 2], name: x}
steps:
  # first comment
  - id: first
    log: start

  - id: outer
    loop:
      times: 2
      do:
        - id: check
          if:
            when: "n > 0"
            then:
              - id: inner
                set: {name: n, expr: "n + 1"}
            else:
              - id: other
                log: other
  - id: last
    log: end
# trailing comment
"""


def tree_of(text: str = SCRIPT) -> list[dict[str, Any]]:
    """The ``steps`` of the engine's ``load`` result for ``text``."""
    registry = default_registry()
    result = load_script(text, lookup=registry.spec, check=registry.check_params)
    assert result.script is not None, result.errors
    return [step_node(step).to_json() for step in result.script.steps]


Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeEngine:
    """Records commands; answers them with :attr:`handlers`; events come from :meth:`emit`."""

    def __init__(self, tree: list[dict[str, Any]], params: list[dict[str, Any]] | None = None,
                 ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.state: dict[str, Any] = {"state": "paused", "variables": {}}
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._seq = itertools.count(1)
        self.handlers: dict[str, Handler] = {
            "load": lambda p: {"script_id": "s1", "steps": tree, "params": params or [],
                               "warnings": []},
            "run": lambda p: {"run_id": "r1"},
            "get_state": lambda p: {"run_id": "r1", **self.state},
        }

    async def call(self, type: str, payload: Mapping[str, Any] | None = None
                   ) -> dict[str, Any]:
        data = dict(payload or {})
        self.calls.append((type, data))
        handler = self.handlers.get(type)
        return handler(data) if handler is not None else {}

    async def events(self) -> AsyncIterator[Event]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self, timeout: float = 5.0) -> int | None:
        self.closed = True
        self._queue.put_nowait(None)
        return 0

    def emit(self, type: str, payload: dict[str, Any] | None = None,
             run_id: str | None = "r1") -> None:
        self._queue.put_nowait(Event(type=type, seq=next(self._seq), ts="", t_ms=0,
                                     run_id=run_id, payload=payload or {}))

    def commands(self, type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == type]
