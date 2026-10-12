"""j5-macro: scriptable, debuggable desktop automation.

"Need input!" — Johnny 5.

The design lives in ``docs/contract.md``. In short: a headless engine process
speaks a JSON-lines protocol (commands in, events out); the CLI, a future GUI
and a DAP debugger are all clients of that protocol.
"""

__version__ = "0.0.1"
