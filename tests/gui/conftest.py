"""Fixtures for the GUI tests.

Qt runs with the ``offscreen`` platform (no display needed; set here, not globally, so
other tests are unaffected). Tests that need Qt start with::

    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")

so they are skipped where the gui-test extra is not installed; the tests of the view
models (test_models.py) and of the engine thread import no Qt and always run.

The GUI talks to a real engine (``python -m j5_macro.engine``) with the fake input
backend (``J5_BACKEND=fake``: records input, touches nothing) and a runs directory under
``tmp_path``. Fixtures, from the bottom up:

- ``engine_env``: the engine's environment.
- ``scripts``: a folder with test scripts (``quick.yaml``, ``slow.yaml``, ``bad.yaml``).
- ``ctx``: an :class:`AppContext` whose engine is started (and closed afterwards).
- ``window``: a shown :class:`MainWindow` on that context.

Helpers and the scripts' text are in gui_support.py (``from gui_support import ...``).
"""

from __future__ import annotations

import faulthandler
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui_support import BAD, QUICK, SLOW, TEST_TIMEOUT, WAIT


@pytest.fixture(autouse=True)
def watchdog() -> Iterator[None]:
    faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def engine_env(tmp_path: Path) -> dict[str, str]:
    env = {**os.environ, "J5_BACKEND": "fake", "J5_RUNS_DIR": str(tmp_path / "runs")}
    for name in ("J5_ENGINE_CMD", "J5_STOP_KEY"):
        env.pop(name, None)
    return env


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    folder = tmp_path / "scripts"
    folder.mkdir()
    for name, text in (("quick.yaml", QUICK), ("slow.yaml", SLOW), ("bad.yaml", BAD)):
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
    return folder


@pytest.fixture
def ctx(qtbot: Any, engine_env: dict[str, str], scripts: Path) -> Iterator[Any]:
    from j5_macro.gui.context import AppContext

    context = AppContext.create(env=engine_env, cwd=str(scripts))
    with qtbot.waitSignal(context.bridge.engine_started, timeout=WAIT):
        context.bridge.start()
    yield context
    context.bridge.shutdown()


@pytest.fixture
def window(qtbot: Any, ctx: Any) -> Any:
    from j5_macro.gui.views.main_window import MainWindow

    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.resize(1200, 800)
    main.show()
    return main
