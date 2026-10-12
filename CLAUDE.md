# j5-macro — notes for coding agents

- `docs/contract.md` is the source of truth for the protocol and architecture. Change it first,
  in the same PR, whenever a command, event, step type or backend primitive changes.
- The engine core never prints or reads stdin directly: all output is a protocol event.
- Backends expose primitives only; human-like behaviour belongs in `j5_macro.input`.
- Input planning must be a pure, seeded function that is testable without a display.
- Everything in the repo is in English: code, identifiers, comments, docstrings, docs,
  test data, commit messages, issues and PRs. The project is meant to be open source.
- Before pushing: `scripts/check.sh` (lint, types, and the tests under Xvfb on Linux) must
  pass. GitHub Actions runs the same script on every PR and push to main
  (`.github/workflows/ci.yml`); merge only when it is green. Keep the workflow lean while
  the repository is private (Actions minutes are metered there, Windows at double rate).
- Supported in v1: Windows and Linux with an X11 session (tested on XFCE). Wayland comes later.
