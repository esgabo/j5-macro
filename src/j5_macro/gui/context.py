"""The services every panel gets: one object, passed to each panel's constructor."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject

from j5_macro.gui.bridge import EngineBridge
from j5_macro.gui.controllers.document import ScriptDocument
from j5_macro.gui.controllers.run import RunController
from j5_macro.gui.screen_tools import ScreenTools


@dataclass(slots=True)
class AppContext:
    bridge: EngineBridge
    """The engine: commands (``call``) and events (``event_received``)."""
    document: ScriptDocument
    """The open script."""
    run: RunController
    """The active run and the breakpoints."""
    screen: ScreenTools
    """Capture, pick, find, screenshot and windows."""

    @classmethod
    def create(cls, engine_cmd: Sequence[str] | None = None, *,
               env: Mapping[str, str] | None = None, cwd: str | None = None,
               parent: QObject | None = None) -> AppContext:
        """Everything wired, the engine not started yet (``ctx.bridge.start()``)."""
        bridge = EngineBridge(engine_cmd, env=env, cwd=cwd, parent=parent)
        document = ScriptDocument(bridge, parent=bridge)
        return cls(bridge=bridge, document=document,
                   run=RunController(bridge, document, parent=bridge),
                   screen=ScreenTools(bridge, parent=bridge))
