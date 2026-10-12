# j5-macro — notes for coding agents

- `docs/contract.md` is the source of truth for the protocol and architecture. Change it first,
  in the same PR, whenever a command, event, step type or backend primitive changes.
- The engine core never prints or reads stdin directly: all output is a protocol event.
- Backends expose primitives only; human-like behaviour belongs in `j5_macro.input`.
- Input planning must be a pure, seeded function that is testable without a display.
- Everything in the repo is in English: code, identifiers, comments, docstrings, docs,
  test data, commit messages, issues and PRs. The project is meant to be open source.
- Before pushing or merging: `scripts/check.sh` (lint, types, and the tests under Xvfb on
  Linux) must pass. There is no hosted CI: GitHub Actions is deliberately not used (it costs
  money); a self-hosted Woodpecker pipeline will run the same script later.
- Supported in v1: Windows and Linux with an X11 session (tested on XFCE). Wayland comes later.
