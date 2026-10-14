"""Shared by the GUI tests: the test scripts and waiting helpers (no Qt import here)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TEST_TIMEOUT = 120
"""Seconds before a hung GUI test dumps its threads and exits."""
WAIT = 15_000
"""Milliseconds to wait for the engine (start, answers, events)."""

QUICK = """\
version: 1
name: Quick
vars:
  n: 0
steps:
  - id: hello
    log: "hello from the GUI"
  - id: repeat
    loop:
      times: 2
      do:
        - id: bump
          set: {name: n, expr: "n + 1"}
        - id: check
          if:
            when: "n == 2"
            then:
              - id: second
                log: "second"
            else:
              - id: first
                log: "first"
  - id: press
    key: ctrl+s
"""

SLOW = """\
version: 1
name: Slow
steps:
  - id: start
    log: "waiting"
  - id: wait
    sleep: 60s
  - id: never
    log: "not reached"
"""

BAD = """\
version: 1
name: Bad
steps:
  - id: oops
    frobnicate: {}
"""


def wait_for(qtbot: Any, condition: Callable[[], object], timeout: int = WAIT) -> None:
    """Process Qt events until ``condition()`` is truthy (fails after ``timeout`` ms)."""
    qtbot.waitUntil(lambda: bool(condition()), timeout=timeout)
