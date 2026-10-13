# Installing and packaging

j5-macro is a pure-Python package (Python 3.11 or later) with a `j5` command. It is not
on PyPI yet, so you install it from GitHub or from a clone.

## Requirements

| Platform | Status | What you need |
| --- | --- | --- |
| Linux with an X11 session (Ubuntu, Debian, XFCE...) | Supported | The `x11` extra; the `vision` extra for image steps |
| Linux with Wayland | Not supported yet | Log in with an "X11"/"Xorg" session instead; dry runs work anywhere |
| Windows 10 and 11 | Supported | The `windows` extra (screen capture); the `vision` extra for image steps |
| macOS | Not a target in v1 | Dry runs and script editing only |

Extras:

| Extra | Installs | For |
| --- | --- | --- |
| `x11` | `python-xlib`, `mss` | Mouse and keyboard input on X11 |
| `vision` | `opencv-python-headless`, `numpy`, `mss` | `wait_image`, image targets, `image_visible` |
| `windows` | `mss` | Screen capture on Windows (the input backend itself needs nothing beyond Python) |
| `dev` | `pytest`, `ruff`, `mypy`, ... | Working on j5 itself ([development.md](development.md)) |

Without any extra, j5 still loads, validates, dry-runs and edits scripts.

## Install from GitHub

In a virtual environment:

```console
$ python3 -m venv ~/.venvs/j5
$ ~/.venvs/j5/bin/pip install "j5-macro[x11,vision] @ git+https://github.com/esgabo/j5-macro"
$ ~/.venvs/j5/bin/j5 --version
j5-macro 0.0.1
```

Or as an isolated command-line tool with [pipx](https://pipx.pypa.io/), which puts `j5` on
your `PATH`:

```console
$ pipx install "j5-macro[x11,vision] @ git+https://github.com/esgabo/j5-macro"
```

On Windows, use the `windows` extra instead of `x11` (in PowerShell, the virtual
environment's commands are under `Scripts\`):

```console
> py -m venv $HOME\.venvs\j5
> ~\.venvs\j5\Scripts\pip install "j5-macro[windows,vision] @ git+https://github.com/esgabo/j5-macro"
> ~\.venvs\j5\Scripts\j5 --version
j5-macro 0.0.1
```

Pin a version by appending a tag or commit: `git+https://github.com/esgabo/j5-macro@<ref>`.

## Install from a clone

For trying the examples or following the [tutorial](tutorial.md):

```console
$ git clone https://github.com/esgabo/j5-macro
$ cd j5-macro
$ python3 -m venv .venv && source .venv/bin/activate
$ pip install -e ".[x11,vision]"
```

`-e` (editable) makes code changes take effect without reinstalling.

## Check the installation

```console
$ j5 --version
$ j5 validate examples/hello.yaml
$ j5 run --dry-run examples/hello.yaml     # sends no input
```

A real run prints the backend in its header (`backend x11`). If it says `backend none`,
see [how-it-works.md](how-it-works.md#platforms-and-backends) for the reason (no X11
session, a Wayland session, or the `x11` extra missing).

## Packaging

### A wheel

The project builds with [hatchling](https://hatch.pypa.io/) into a single pure-Python wheel
(`py3-none-any`, about 240 KB) that installs on any platform:

```console
$ pip wheel --no-deps -w dist .        # or: python -m build
$ ls dist
j5_macro-0.0.1-py3-none-any.whl
$ pip install "dist/j5_macro-0.0.1-py3-none-any.whl[x11,vision]"
```

You can copy that wheel to another machine, put it in an internal package index, or
attach it to a GitHub release. Its dependencies (OpenCV, python-xlib...) come from PyPI
when it is installed.

### PyPI

The metadata in `pyproject.toml` is ready for PyPI (name `j5-macro`, Apache-2.0 license,
`j5` console script). Publishing would make the install simply
`pip install "j5-macro[x11,vision]"` or `pipx install "j5-macro[x11,vision]"`. It has not
been published yet.

### Standalone executables

A single-file executable (PyInstaller, Nuitka) is possible but not provided: the `j5`
command starts the engine as a second process (`python -m j5_macro.engine`), so a bundle
must also contain the engine and point `J5_ENGINE_CMD` at it, and OpenCV makes it large
(around 100 MB). For most uses pipx gives the same convenience with less effort.

### Containers

j5 can run in a container that has an X server, for example Xvfb for tests or CI:

```console
$ xvfb-run -a -s "-screen 0 1280x800x24" j5 run script.yaml
```

It then automates the container's own virtual display, not your desktop. To automate a
real desktop the process must reach that desktop's X server (`DISPLAY` and its
authorization), which is how j5's own end-to-end tests run.

## Uninstall

```console
$ pipx uninstall j5-macro      # or: pip uninstall j5-macro
```

Run history is kept separately (see [how-it-works.md](how-it-works.md#run-history)); delete
that folder too if you want everything gone.
