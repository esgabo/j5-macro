"""View models and state of the GUI: plain Python, no Qt.

Everything here is built from engine results and events (docs/contract.md) and is unit
tested without a display. Widgets in :mod:`j5_macro.gui.views` render these objects;
controllers in :mod:`j5_macro.gui.controllers` keep them up to date.
"""

from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.gui.models.run_log import LogLine, RunLog, format_event
from j5_macro.gui.models.runs import RunSummary, parse_run_list
from j5_macro.gui.models.script import ScriptEntry, ScriptState, parse_script_list
from j5_macro.gui.models.steps import StepItem, find_step, iter_steps, parse_steps, step_summary

__all__ = [
    "EngineInfo",
    "LogLine",
    "RunLog",
    "RunSummary",
    "ScriptEntry",
    "ScriptState",
    "StepItem",
    "find_step",
    "format_event",
    "iter_steps",
    "parse_run_list",
    "parse_script_list",
    "parse_steps",
    "step_summary",
]
