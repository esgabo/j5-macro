"""Entry point for the ``j5`` command.

Exit codes (every command):

- 0: success (``validate``: the script is valid, warnings allowed; ``run``: completed;
  ``find``: the image was found).
- 1: ``validate``: the script is invalid or cannot be read; ``run``: the run failed;
  ``runs show``/``runs delete``: no saved run with that id; ``pack``/``unpack``: the
  script is invalid, an image cannot be embedded or a file is in the way; ``find``: the
  image was not found; the authoring commands: the engine refused (e.g. no vision, an
  existing file); any command: the engine could not be started or died.
- 2: usage error; ``run``: the script could not be loaded or the run could not start;
  ``runs show``/``runs delete``: a malformed run id.
- 3: ``run``: the run was stopped (by the engine, e.g. user interference);
  ``capture``/``pick``/``find --show``: cancelled with Escape.
- 130: interrupted with Ctrl+C.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import json
import os
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from j5_macro import __version__
from j5_macro.cli import commands
from j5_macro.cli.commands import CliError, ExitCode, Output, RunOptions
from j5_macro.cli.options import (
    FAST_PROFILE,
    collect_params,
    engine_command,
    parse_param,
    parse_param_env,
    parse_param_file,
    parse_var,
)
from j5_macro.cli.render import Style, symbols_for, use_color
from j5_macro.engine.backend import DEFAULT_STOP_KEY, STOP_KEY_ENV, parse_combo
from j5_macro.engine.persistence import RUNS_DIR_ENV, RUNS_KEPT, resolve_runs_dir

EXIT_CODES = """\
exit codes:
  0    completed
  1    the run failed, a paused step kept failing with no terminal to ask,
       or the engine died
  2    usage error, or the script could not be loaded or started
  3    the run was stopped (by the engine, or with q in the debugger)
  130  interrupted with Ctrl+C (the first Ctrl+C stops the run and waits for it,
       the second one quits at once)"""

PARAMS_HELP = """\
script params:
  Values declared in the script's 'params' section, used in steps as ${name}.
  Each param comes from one of --param, --param-file, --param-env or --params-file.
  Required params left out are asked for when standard input is a terminal; secret
  ones without echo. Pass secrets with --param-env, --param-file or the prompt: a
  value on the command line is visible in the shell history and the process list.
  Secret values are shown as *** in the output and the run history.

  j5 run form.yaml --param 'customer=ACME #42' --param-env password=FORM_PASSWORD
  j5 run letter.yaml --param-file body=letter.txt
  printf %s "$TOKEN" | j5 run login.yaml --param-file token=-
"""

DEBUG_HELP = """\
debugging:
  --step starts paused before the first step; --break STEP_ID pauses before that step.
  At each pause the debugger reads commands (h lists them): n(ext), s(tep), o(ut),
  c(ontinue), p(rint) EXPR, v(ars), set NAME = EXPR, b(reak) [ID], clear [ID], w(here),
  l(ist), q(uit). After a failed step or mouse interference, c retries it and n skips it.

  j5 run --step script.yaml
  printf 'n\\nvars\\nc\\n' | j5 run --step script.yaml
"""

SCHEMA_HELP = """\
Print the JSON Schema of j5 scripts, for editor completion and validation.

With the VS Code YAML extension (redhat.vscode-yaml), save it to a file:

  j5 schema -o j5-script.schema.json

and either start each script with this comment:

  # yaml-language-server: $schema=./j5-script.schema.json

or map it to your script files in .vscode/settings.json:

  "yaml.schemas": {"./j5-script.schema.json": ["*.j5.yaml", "scripts/**/*.yaml"]}"""


PACK_HELP = """\
Embed every image file the script references into its 'images' section, so the
script can be shared as a single file. Each image parameter written as a path
(img/button.png) becomes a reference to the embedded copy ("#button"); names come
from the file stems, and identical files share one entry. Paths built with ${...}
are left as they are. Comments and formatting are kept.

  j5 pack save_report.yaml -o save_report.bundle.yaml
  j5 pack save_report.yaml -o save_report.yaml        (in place)

exit codes: 0 done, 1 invalid script or an image that cannot be embedded (missing,
not a PNG file, over 1 MiB, or over 8 MiB in all), 2 usage error"""

UNPACK_HELP = """\
Write the embedded images of a script to DIR/img/<name>.png and the script to
DIR/<script file name>, with every "#name" reference turned back into the path
img/<name>.png and the 'images' section removed. Existing files are not
overwritten without --force.

  j5 unpack save_report.bundle.yaml -d save_report/

exit codes: 0 done, 1 invalid script, no embedded images or a file in the way,
2 usage error"""


RUNS_HELP = f"""\
Every run is saved in the runs directory: --runs-dir, else ${RUNS_DIR_ENV}, else the
platform state directory (~/.local/state/j5-macro/runs on Linux,
%LOCALAPPDATA%\\j5-macro\\runs on Windows). The newest {RUNS_KEPT} runs are kept.

  j5 runs                       list the saved runs, newest first
  j5 runs show RUN_ID           replay a run's events as j5 run showed them
  j5 runs show --json RUN_ID    its events, one JSON line each
  j5 runs delete RUN_ID...      delete runs

exit codes: 0 done, 1 no saved run with that id (or the engine died), 2 usage error or
a malformed run id"""


def _runs_dir_option() -> argparse.ArgumentParser:
    """``--runs-dir``, accepted before or after the ``runs`` subcommand's own command."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--runs-dir", metavar="DIR", default=argparse.SUPPRESS,
                        help=f"the runs directory (default: ${RUNS_DIR_ENV}, else the "
                             "platform state directory)")
    return parent


AUTHORING_HELP = """\
Writing scripts faster: these commands read the screen (they need the vision extra and
a display) and send no input. capture, pick and find print the YAML of a step to paste
into a script; --json prints the engine's result instead.

  j5 windows                                       visible windows and client areas
  j5 screenshot --window Notes                     a PNG of a window, for a look
  j5 capture -o img/save.png                       drag a rectangle, save a template
  j5 capture --embed s.yaml --name save --bbox 10,20,32,24 --window Notes
  j5 pick --window Notes                           click a point inside a window
  j5 find img/save.png --show                      test a template, see the match
  j5 find '#save' --script s.yaml --window Notes   test an embedded one

capture, pick and find --show open a full-screen overlay: drag or click with the left
button; Escape or the right button cancels.

exit codes: 0 done (find: found), 1 failure (find: not found), 2 usage error,
3 cancelled with Escape"""


def _rect(text: str) -> list[int]:
    """``--bbox`` and ``--region``: ``x,y,w,h``."""
    from j5_macro.cli.authoring import parse_rect

    try:
        return parse_rect(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _authoring_parsers(sub: Any) -> None:
    """``windows``, ``screenshot``, ``capture``, ``pick`` and ``find``."""
    formatter = argparse.RawDescriptionHelpFormatter
    windows = sub.add_parser(
        "windows", help="list the visible windows and their client areas",
        description="List the visible windows, topmost first, with the client area of "
                    "each on the desktop as [x, y, width, height].",
        epilog=AUTHORING_HELP, formatter_class=formatter)
    windows.add_argument("--json", action="store_true", help="print the engine's answer")

    shot = sub.add_parser(
        "screenshot", help="save a PNG of the desktop, a window or a region",
        description="Save a PNG of the whole desktop, of a window's client area or of a "
                    "region, and print its path.",
        epilog=AUTHORING_HELP, formatter_class=formatter)
    shot.add_argument("-o", "--output", metavar="FILE",
                      help="where to save it (default: a new file in the system's "
                           "temporary directory); an existing file is replaced")
    shot.add_argument("--window", metavar="TITLE",
                      help="the window whose title contains TITLE: its client area")
    shot.add_argument("--region", metavar="X,Y,W,H", type=_rect,
                      help="only this rectangle (relative to --window when given)")
    shot.add_argument("--json", action="store_true", help="print the engine's answer")

    cap = sub.add_parser(
        "capture", help="capture a template: drag a rectangle on the screen",
        description="Capture a rectangle of the screen as a template, into a PNG file or "
                    "into a script's images section, and print a click step that uses it. "
                    "Without --bbox the screen freezes and you drag the rectangle (a "
                    "magnifier shows the pixels under the pointer).",
        epilog=AUTHORING_HELP, formatter_class=formatter)
    destination = cap.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output", metavar="FILE", help="save it as a PNG file")
    destination.add_argument("--embed", metavar="SCRIPT",
                             help="embed it in this script's images section (with --name)")
    cap.add_argument("--name", metavar="NAME", help="the embedded image's name (#NAME)")
    cap.add_argument("--window", metavar="TITLE",
                     help="the window whose title contains TITLE: --bbox is relative to "
                          "its client area")
    cap.add_argument("--bbox", metavar="X,Y,W,H", type=_rect,
                     help="capture this rectangle without the overlay (for scripts and "
                          "agents)")
    cap.add_argument("--padding", metavar="PIXELS", type=int, default=0,
                     help="grow the rectangle by this many pixels on every side")
    cap.add_argument("--transparent", action="store_true",
                     help="make the pixels of the border's colour transparent, for icons "
                          "on backgrounds that change")
    cap.add_argument("-f", "--force", action="store_true",
                     help="replace an existing file or embedded image")
    cap.add_argument("--json", action="store_true", help="print the engine's answer")

    pick = sub.add_parser(
        "pick", help="pick a point on the screen and print a click step",
        description="Click a point on the frozen screen and print a click step for it, "
                    "relative to the window under it (or --window).",
        epilog=AUTHORING_HELP, formatter_class=formatter)
    pick.add_argument("--window", metavar="TITLE",
                      help="report the point relative to this window's client area")
    pick.add_argument("--json", action="store_true", help="print the engine's answer")

    find = sub.add_parser(
        "find", help="search the screen for a template, as a step would",
        description="Search the screen once for IMAGE, exactly as a step would, without "
                    "running anything, and print a click step when it is found.",
        epilog=AUTHORING_HELP, formatter_class=formatter)
    find.add_argument("image", metavar="IMAGE",
                      help="a PNG file, or #NAME for an image embedded in --script")
    find.add_argument("--script", metavar="SCRIPT",
                      help="search as this script's steps do: IMAGE relative to its "
                           "directory, #NAME from its images, its capture_scale")
    find.add_argument("--window", metavar="TITLE", help="search this window's client area")
    find.add_argument("--region", metavar="X,Y,W,H", type=_rect,
                      help="search this rectangle (relative to --window when given)")
    find.add_argument("--threshold", type=float, default=0.9,
                      help="minimum score, 0 to 1 (default 0.9)")
    find.add_argument("--scale", type=float, default=1.0,
                      help="resize the template by this factor too (default 1)")
    find.add_argument("--display-scale", metavar="SCALE", type=_display_scale,
                      help="the display scale to assume, e.g. 1.5 or 150%%, instead of "
                           "detecting it")
    find.add_argument("--show", action="store_true",
                      help="highlight the best match on the screen for a few seconds: "
                           "green when found, red with the score when not")
    find.add_argument("--show-ms", metavar="MS", type=int, default=3000,
                      help="how long --show shows it (default 3000)")
    find.add_argument("--json", action="store_true", help="print the engine's answer")


def _display_scale(text: str) -> float:
    """``--display-scale``: a positive number, ``1.5`` or ``150%``."""
    value = text.strip()
    percent = value.endswith("%")
    try:
        number = float(value[:-1] if percent else value)
    except ValueError:
        number = float("nan")
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError(
            f"expected a positive number such as 1.5 or 150%, got {text!r}")
    return number / 100 if percent else number


def _stop_key(text: str) -> str:
    try:
        parse_combo(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="j5", description="Need input! Desktop automation.")
    parser.add_argument("--version", action="version", version=f"j5-macro {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    runs_dir = _runs_dir_option()

    run = sub.add_parser(
        "run", help="run a script", parents=[runs_dir],
        description="Load a script, run it and show its progress.",
        epilog=DEBUG_HELP + "\n" + PARAMS_HELP + "\n" + EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_argument("script", help="path to a YAML script")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--step", action="store_true",
                      help="debug the script: start paused before the first step and give a "
                           "debugger prompt whenever the run pauses (n, s, c, p, vars, h for "
                           "help); commands are read from standard input, so they can be "
                           "piped, and the run continues to the end when they run out")
    mode.add_argument("--dry-run", action="store_true",
                      help="send no input; image searches are tried once")
    run.add_argument("--strict", action="store_true",
                     help="with --dry-run: fail the step, and the run, where a dry run "
                          "would only warn that the real run fails (an image not found, "
                          "a missing window, vision not available, exec not allowed)")
    run.add_argument("--break", dest="breakpoints", metavar="STEP_ID", action="append",
                     default=[],
                     help="pause before the step with this id and give the debugger prompt "
                          "(with or without --step); repeatable")
    run.add_argument("--seed", type=int, help="seed for human-like input")
    run.add_argument("--fast", action="store_true",
                     help="force the direct input strategy for mouse and keyboard")
    run.add_argument("--allow-exec", action="store_true",
                     help="let the script's exec steps run programs (without it they fail "
                          "with capability_missing)")
    run.add_argument("--display-scale", metavar="SCALE", type=_display_scale,
                     help="the display scale of this run, e.g. 1.5 or 150%%, instead of "
                          "detecting it per monitor; templates and pixel offsets are "
                          "scaled by it over the script's capture_scale")
    run.add_argument("--param", dest="params", metavar="NAME=VALUE", action="append",
                     type=parse_param, default=[],
                     help="set a script param to VALUE, taken as text exactly as written "
                          "(converted for integer, number and boolean params); repeatable")
    run.add_argument("--param-file", dest="param_files", metavar="NAME=PATH",
                     action="append", type=parse_param_file, default=[],
                     help="set a param to the contents of a UTF-8 file (- for standard "
                          "input), without its final line break; repeatable")
    run.add_argument("--param-env", dest="param_envs", metavar="NAME=VARIABLE",
                     action="append", type=parse_param_env, default=[],
                     help="set a param from an environment variable; repeatable")
    run.add_argument("--params-file", metavar="FILE",
                     help="set params from a YAML or JSON mapping of names to values")
    run.add_argument("--var", dest="vars", metavar="NAME=VALUE", action="append",
                     type=parse_var, default=[],
                     help="override one of the script's vars (for debugging); 3, 2.5, "
                          "true, false and null are converted, '...' forces text, anything "
                          "else is text as written; repeatable")
    run.add_argument("--captures", choices=("on_failure", "all"),
                     help="which screen captures of image searches the run saves: "
                          "on_failure (default: the last attempt of a search that fails) "
                          "or all (every attempt)")
    run.add_argument("--stop-key", metavar="COMBO", type=_stop_key,
                     help=f"global emergency stop key for this run's engine, e.g. "
                          f"ctrl+alt+f12 (default: ${STOP_KEY_ENV}, else {DEFAULT_STOP_KEY})")
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

    runs = sub.add_parser(
        "runs", help="list, show and delete saved runs", parents=[runs_dir],
        description=RUNS_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    runs.add_argument("-n", "--limit", type=int, default=RUNS_KEPT,
                      help=f"show at most this many runs (default {RUNS_KEPT})")
    runs.add_argument("--json", action="store_true", help="print the engine's answer as JSON")
    runs_sub = runs.add_subparsers(dest="runs_command", metavar="<action>")
    show = runs_sub.add_parser("show", parents=[runs_dir],
                               help="show a saved run's details and events")
    show.add_argument("run_id", metavar="RUN_ID")
    show.add_argument("--json", dest="show_json", action="store_true",
                      help="print the run's events as JSON lines instead of text")
    delete = runs_sub.add_parser("delete", parents=[runs_dir], help="delete saved runs")
    delete.add_argument("run_ids", metavar="RUN_ID", nargs="+")

    steps = sub.add_parser("steps", help="list the step types and their parameters")
    steps.add_argument("--json", action="store_true", help="print the engine's answer as JSON")

    schema = sub.add_parser(
        "schema", help="print the JSON Schema of scripts for editors",
        description=SCHEMA_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    schema.add_argument("-o", "--output", metavar="FILE",
                        help="write the schema to FILE instead of stdout")

    pack = sub.add_parser(
        "pack", help="embed the image files a script uses into the script",
        description=PACK_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    pack.add_argument("script", help="path to a YAML script")
    pack.add_argument("-o", "--output", metavar="FILE",
                      help="write the packed script to FILE (it may be the script itself) "
                           "instead of stdout")

    unpack = sub.add_parser(
        "unpack", help="write a script's embedded images back to files",
        description=UNPACK_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    unpack.add_argument("script", help="path to a YAML script with an images section")
    unpack.add_argument("-d", "--directory", metavar="DIR", required=True,
                        help="where to write the script and its img/ directory")
    unpack.add_argument("-f", "--force", action="store_true",
                        help="overwrite files that already exist")

    _authoring_parsers(sub)

    sub.add_parser("dap", help="serve the Debug Adapter Protocol on stdio (for VS Code)",
                   description="Debug Adapter Protocol server on standard input and output, "
                               "started by an editor such as VS Code (see editors/vscode).")
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


def _ask(prompt: str, secret: bool) -> str:
    """Ask for a param on the terminal (without echo for secrets)."""
    if secret:
        return getpass.getpass(prompt, stream=sys.stderr)
    print(prompt, end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _schema(output: str | None) -> int:
    from j5_macro.steps.builtins import registry_with_plugins
    from j5_macro.steps.schema import script_json_schema

    registry, report = registry_with_plugins()
    for problem in report.skipped:
        print(f"j5: warning: {problem}", file=sys.stderr)
    text = json.dumps(script_json_schema(registry), indent=2, ensure_ascii=False)
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


def _runs(args: argparse.Namespace, cmd: list[str], runs_dir: str, io: Output) -> int:
    if args.runs_command == "show":
        return commands.run_sync(commands.runs_show(cmd, args.run_id, as_json=args.show_json,
                                                    runs_dir=runs_dir, io=io))
    if args.runs_command == "delete":
        return commands.run_sync(commands.runs_delete(cmd, args.run_ids, io=io))
    if args.limit < 0:
        raise CliError("--limit must not be negative", ExitCode.USAGE)
    return commands.run_sync(commands.runs_list(cmd, limit=args.limit, as_json=args.json,
                                                runs_dir=runs_dir, io=io))


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "schema":
        return _schema(args.output)
    if args.command in ("pack", "unpack"):
        from j5_macro.cli import bundle

        if args.command == "pack":
            return bundle.pack(args.script, args.output, out=sys.stdout, err=sys.stderr,
                               style=_style(sys.stderr))
        return bundle.unpack(args.script, args.directory, force=args.force, err=sys.stderr,
                             style=_style(sys.stderr))
    if args.command == "dap":
        from j5_macro.dap.server import main as dap_main

        return dap_main()
    try:
        cmd = engine_command()
    except ValueError as exc:
        raise CliError(str(exc), ExitCode.USAGE) from exc
    runs_dir: str | None = getattr(args, "runs_dir", None)
    if runs_dir:
        runs_dir = os.path.abspath(runs_dir)
        cmd += ["--runs-dir", runs_dir]
    stop_key: str | None = getattr(args, "stop_key", None)
    if stop_key:
        cmd += ["--stop-key", stop_key]
    io = Output(out=sys.stdout, err=sys.stderr, style=_style(sys.stdout),
                err_style=_style(sys.stderr))
    if args.command == "runs":
        return _runs(args, cmd, str(resolve_runs_dir(runs_dir)), io)
    if args.command == "validate":
        return commands.run_sync(commands.validate(cmd, args.script, as_json=args.json, io=io))
    if args.command == "steps":
        return commands.run_sync(commands.steps(cmd, as_json=args.json, io=io))
    if args.command in AUTHORING_COMMANDS:
        return _authoring(args, cmd, io)
    if args.strict and not args.dry_run:
        raise CliError("--strict only applies with --dry-run", ExitCode.USAGE)
    variables: dict[str, Any] = dict(args.vars)
    try:
        params = collect_params(args.params_file, args.params, args.param_files,
                                args.param_envs, environ=os.environ, stdin=sys.stdin)
    except ValueError as exc:
        raise CliError(str(exc), ExitCode.USAGE) from exc
    reads_stdin = any(path == "-" for _, path in args.param_files)
    stdin_tty = _stdin_is_tty()
    interactive = stdin_tty and not reads_stdin
    options = RunOptions(
        mode="dry" if args.dry_run else "step" if args.step else "normal",
        debug=args.step or bool(args.breakpoints),
        breakpoints=list(args.breakpoints),
        echo_commands=not stdin_tty,
        params=params,
        inline_params=frozenset(name for name, _ in args.params),
        ask=_ask if interactive else None,
        vars=variables,
        seed=args.seed,
        input_profile=copy.deepcopy(FAST_PROFILE) if args.fast else None,
        captures=args.captures,
        allow_exec=args.allow_exec,
        display_scale=args.display_scale,
        strict=args.strict,
        as_json=args.json,
        interactive=interactive,
    )
    return commands.run_sync(commands.run(cmd, args.script, options, io=io))


AUTHORING_COMMANDS = ("windows", "screenshot", "capture", "pick", "find")


def _authoring(args: argparse.Namespace, cmd: list[str], io: Output) -> int:
    from j5_macro.cli import authoring

    if args.command == "windows":
        return commands.run_sync(authoring.windows(cmd, as_json=args.json, io=io))
    if args.command == "screenshot":
        return commands.run_sync(authoring.screenshot(
            cmd, output=args.output, window=args.window, region=args.region,
            as_json=args.json, io=io))
    if args.command == "capture":
        if args.padding < 0:
            raise CliError("--padding must not be negative", ExitCode.USAGE)
        options = authoring.CaptureOptions(
            output=args.output, embed=args.embed, name=args.name, window=args.window,
            bbox=args.bbox, padding=args.padding, transparent=args.transparent,
            force=args.force, as_json=args.json)
        return commands.run_sync(authoring.capture(cmd, options, io=io))
    if args.command == "pick":
        return commands.run_sync(authoring.pick(cmd, window=args.window, as_json=args.json,
                                                io=io))
    if not 0 <= args.threshold <= 1:
        raise CliError("--threshold must be between 0 and 1", ExitCode.USAGE)
    if not 0 < args.scale < float("inf"):
        raise CliError("--scale must be a positive number", ExitCode.USAGE)
    if args.show_ms < 0:
        raise CliError("--show-ms must not be negative", ExitCode.USAGE)
    find = authoring.FindOptions(
        script=args.script, window=args.window, region=args.region,
        threshold=args.threshold, scale=args.scale, display_scale=args.display_scale,
        show=args.show, show_ms=args.show_ms, as_json=args.json)
    return commands.run_sync(authoring.find(cmd, args.image, find, io=io))


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
    except BrokenPipeError:
        # The reader went away (``j5 runs show --json | head``): stop quietly. Point
        # stdout at devnull so the interpreter's final flush does not fail again.
        _silence_stdout()
        return ExitCode.FAILED


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
