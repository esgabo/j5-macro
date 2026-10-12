# j5-macro — notes for coding agents

- `docs/contract.md` is the source of truth for the protocol and architecture. Change it first,
  in the same PR, whenever a command, event, step type or backend primitive changes.
- The engine core never prints or reads stdin directly: all output is a protocol event.
- Backends expose primitives only; human-like behaviour belongs in `j5_macro.input`.
- Input planning must be a pure, seeded function that is testable without a display.
- Code, identifiers and commit messages in English; the design docs are in Spanish.
- Before pushing: `ruff check . && mypy && pytest`.
- Supported in v1: Windows and Linux with an X11 session (tested on XFCE). Wayland comes later.
