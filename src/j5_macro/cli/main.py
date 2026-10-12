"""Entry point for the ``j5`` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from j5_macro import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="j5", description="Need input! Desktop automation.")
    parser.add_argument("--version", action="version", version=f"j5-macro {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    run = sub.add_parser("run", help="run a script")
    run.add_argument("script", help="path to a YAML script")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--step", action="store_true", help="start paused, step by step")
    mode.add_argument("--dry-run", action="store_true", help="send no input")
    run.add_argument("--seed", type=int, help="seed for human-like input")
    run.add_argument("--fast", action="store_true", help="force the direct input strategy")

    validate = sub.add_parser("validate", help="validate a script without running it")
    validate.add_argument("script", help="path to a YAML script")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    print(f"j5 {args.command}: not implemented yet (see docs/contract.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
