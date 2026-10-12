"""Entry point for the ``j5`` command.

Exit codes (every command):

- 0: success (``validate``: the script is valid, warnings allowed; ``run``: completed).
- 1: ``validate``: the script is invalid or cannot be read; ``run``: the run failed;
  any command: the engine could not be started or died.
- 2: usage error; ``run``: the script could not be loaded or the run could not start.
- 3: ``run``: the run was stopped (by the engine, e.g. user interference).
- 130: interrupted with Ctrl+C.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from j5_macro import __version__
from j5_macro.cli import commands
from j5_macro.cli.commands import CliError, ExitCode, Output, RunOptions
from j5_macro.cli.options import FAST_PROFILE, engine_command, parse_var
from j5_macro.cli.render import Style, symbols_for, use_color

EXIT_CODES = """\
exit codes:
  0    completed
  1    the run failed, or the engine died
  2    usage error, or the script could not be loaded or started
  3    the run was stopped
  130  interrupted with Ctrl+C (the first Ctrl+C stops the run and waits for it,
       the second one quits at once)"""

SCHEMA_HELP = """\
Print the JSON Schema of j5 scripts, for editor completion and validation.

With the VS Code YAML extension (redhat.vscode-yaml), save it to a file:

  j5 schema -o j5-script.schema.json

and either start each script with this comment:

  # yaml-language-server: $schema=./j5-script.schema.json

or map it to your script files in .vscode/settings.json:

  "yaml.schemas": {"./j5-script.schema.json": ["*.j5.yaml", "scripts/**/*.yaml"]}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="j5", description="Need input! Desktop automation.")
    parser.add_argument("--version", action="version", version=f"j5-macro {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    run = sub.add_parser(
        "run", help="run a script",
        description="Load a script, run it and show its progress.",
        epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_argument("script", help="path to a YAML script")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--step", action="store_true",
                      help="start paused, step by step (not available yet)")
    mode.add_argument("--dry-run", action="store_true",
                      help="send no input; image searches are tried once")
    run.add_argument("--seed", type=int, help="seed for human-like input")
    run.add_argument("--fast", action="store_true",
                     help="force the direct input strategy for mouse and keyboard")
    run.add_argument("--var", dest="vars", metavar="NAME=VALUE", action="append",
                     type=parse_var, default=[],
                     help="set a variable; VALUE is a YAML scalar (3, 2.5, true, text); "
                          "repeatable")
    run.add_argument("--json", action="store_true",
                     help="print every event as one JSON line instead of text")

    validate = sub.add_parser(
        "validate", help="validate a script without running it",
        description="Check a script and report its problems as path:line: messages.",
        epilog="exit codes: 0 valid (warnings allowed), 1 invalid or unreadable "
               "(e.g. script_not_found), 2 usage error",
    )
    validate.add_argument("script", help="path to a YAML script")
    validate.add_argument("--json", action="store_true",
                          help="print one JSON object with errors and warnings")

    steps = sub.add_parser("steps", help="list the step types and their parameters")
    steps.add_argument("--json", action="store_true", help="print the engine's answer as JSON")

    schema = sub.add_parser(
        "schema", help="print the JSON Schema of scripts for editors",
        description=SCHEMA_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    schema.add_argument("-o", "--output", metavar="FILE",
                        help="write the schema to FILE instead of stdout")
    return parser


def _style(stream: TextIO) -> Style:
    color = use_color(stream)
    if color:
        color = _enable_ansi()
    return Style(color=color, symbols=symbols_for(stream))


def _enable_ansi() -> bool:
    """Make sure the console interprets ANSI colors; False if it cannot."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    return True


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _schema(output: str | None) -> int:
    from j5_macro.steps.builtins import default_registry
    from j5_macro.steps.schema import script_json_schema

    text = json.dumps(script_json_schema(default_registry()), indent=2, ensure_ascii=False)
    if output is None:
        print(text)
        return ExitCode.OK
    try:
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    except OSError as exc:
        raise CliError(f"cannot write {output}: {exc}") from exc
    print(f"wrote {output}", file=sys.stderr)
    return ExitCode.OK


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "schema":
        return _schema(args.output)
    if args.command == "run" and args.step:
        print("j5 run --step: the interactive debugger is not available yet (milestone M4); "
              "run without --step, or use --dry-run to check a script without input",
              file=sys.stderr)
        return ExitCode.USAGE
    try:
        cmd = engine_command()
    except ValueError as exc:
        raise CliError(str(exc), ExitCode.USAGE) from exc
    io = Output(out=sys.stdout, err=sys.stderr, style=_style(sys.stdout),
                err_style=_style(sys.stderr))
    if args.command == "validate":
        return commands.run_sync(commands.validate(cmd, args.script, as_json=args.json, io=io))
    if args.command == "steps":
        return commands.run_sync(commands.steps(cmd, as_json=args.json, io=io))
    variables: dict[str, Any] = dict(args.vars)
    options = RunOptions(
        mode="dry" if args.dry_run else "normal",
        vars=variables,
        seed=args.seed,
        input_profile=copy.deepcopy(FAST_PROFILE) if args.fast else None,
        as_json=args.json,
        interactive=_stdin_is_tty(),
    )
    return commands.run_sync(commands.run(cmd, args.script, options, io=io))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return ExitCode.OK
    try:
        return int(_dispatch(args))
    except CliError as exc:
        print(f"j5: {exc}", file=sys.stderr)
        return int(exc.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
