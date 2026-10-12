#!/usr/bin/env bash
# Run every check a CI pipeline would run: lint, types and the full test suite.
#
# On Linux the tests run inside a virtual X server (Xvfb), so the X11 backend and the
# end-to-end tests that really click, type and search the screen run too; they fail
# instead of being skipped when no X server is available. Elsewhere they are skipped.
#
# Usage: scripts/check.sh            (from the repository root, inside the venv)
# Needs: pip install -e ".[dev,x11,vision]" and, on Linux, the xvfb package.
set -euo pipefail

ruff check .
mypy

if [[ "$(uname -s)" == "Linux" ]]; then
  if ! command -v xvfb-run >/dev/null; then
    echo "check.sh: xvfb-run not found; install the xvfb package" >&2
    exit 2
  fi
  J5_REQUIRE_X11=1 xvfb-run -a -s "-screen 0 1280x800x24" pytest -rs "$@"
else
  pytest -rs "$@"
fi
