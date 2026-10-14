"""The agent skill in skills/j5-macro stays true to j5: its frontmatter is what agents
expect, its example scripts validate and are self-contained, its links resolve, and
every ``j5`` command and option it shows exists."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from j5_macro.cli.main import build_parser
from j5_macro.script import load_script
from j5_macro.steps import default_registry

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "j5-macro"
MARKDOWN = sorted(SKILL.rglob("*.md"))
EXAMPLES = sorted((SKILL / "examples").glob("*.yaml"))
FENCE = re.compile(r"^```(\w*)\s*$")
SHELL_BLOCKS = {"console", "text", "bash", "sh", "shell"}


def frontmatter() -> dict[str, object]:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md must start with a YAML frontmatter block between --- lines"
    data = YAML(typ="safe").load(match.group(1))
    assert isinstance(data, dict)
    return data


def test_frontmatter_has_name_and_description() -> None:
    data = frontmatter()
    assert data.get("name") == "j5-macro"
    description = data.get("description")
    assert isinstance(description, str)
    assert 0 < len(description) <= 1024
    for word in ("j5", "YAML", "desktop"):
        assert word.lower() in description.lower()


def test_skill_md_stays_short() -> None:
    lines = (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 320


def test_there_are_examples() -> None:
    assert len(EXAMPLES) >= 3


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_validates(script: Path) -> None:
    registry = default_registry()
    result = load_script(script.read_text(encoding="utf-8"), lookup=registry.spec,
                         check=registry.check_params, source_path=str(script))
    assert result.ok, [e.message for e in result.errors]
    assert result.script is not None
    assert not result.script.warnings, [w.message for w in result.script.warnings]


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_is_self_contained(script: Path) -> None:
    """Every template is embedded, so the example works wherever it is copied."""
    text = script.read_text(encoding="utf-8")
    refs = re.findall(r"\b(?:image|image_visible):\s*([^,}\n]+)", text)
    assert refs
    assert all(ref.strip().startswith('"#') for ref in refs), refs


@pytest.mark.parametrize("doc", MARKDOWN, ids=lambda p: str(p.relative_to(SKILL)))
def test_relative_links_resolve(doc: Path) -> None:
    text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
    for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        assert (doc.parent / target.partition("#")[0]).exists(), target


def _commands(doc: Path) -> list[str]:
    """The j5 command lines a file shows: inline code that starts with ``j5``, the
    commands of console blocks (``$`` lines), lines of text blocks that start with
    ``j5``, and, in YAML, the commands written in comments."""
    lines: list[str] = []
    text = doc.read_text(encoding="utf-8")
    if doc.suffix == ".yaml":
        lines = [re.sub(r"^#\s*", "", line) for line in text.splitlines()
                 if line.startswith("#")]
    else:
        block: str | None = None
        for line in text.splitlines():
            fence = FENCE.match(line.strip())
            if fence:
                block = None if block is not None else fence.group(1)
                continue
            if block is None:
                lines += re.findall(r"`(j5 [^`]+)`", line)
            elif block == "console" and line.startswith("$ "):
                lines.append(line[2:])
            elif block == "console" and lines and lines[-1].endswith("\\"):
                lines.append(line)
            elif block in SHELL_BLOCKS - {"console"}:
                if line[:1].isspace() and lines:  # a synopsis continued on the next line
                    lines[-1] += " " + line.strip()
                else:
                    lines.append(line)
    joined = "\n".join(lines).replace("\\\n", " ")
    found = []
    for line in joined.splitlines():
        for match in re.finditer(r"(?:^|(?<=[\s(|]))j5 ([a-z].*)", line):
            found.append("j5 " + re.split(r";|>|<|&&|\s#|\|\s*(?=[a-z])", match.group(1))[0])
    return found


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {flag for action in parser._actions for flag in action.option_strings}


SOURCES = [*MARKDOWN, *EXAMPLES]


@pytest.mark.parametrize("doc", SOURCES, ids=lambda p: str(p.relative_to(SKILL)))
def test_every_command_and_option_shown_exists(doc: Path) -> None:
    commands = _subcommands(build_parser())
    problems = []
    for line in _commands(doc):
        words = line.split()
        name = words[1]
        if name not in commands:
            problems.append(f"{line}: no command j5 {name}")
            continue
        parser = commands[name]
        options = _options(parser)
        nested = _subcommands(parser)
        if len(words) > 2 and words[2] in nested:
            options |= _options(nested[words[2]])
        for flag in re.findall(r"(?<![\w-])(--?[a-zA-Z][a-zA-Z-]*)", " ".join(words[2:])):
            if flag not in options:
                problems.append(f"{line}: j5 {name} has no option {flag}")
    assert not problems, "\n".join(problems)


def test_every_long_option_mentioned_exists() -> None:
    """Options named outside a full command line (in tables and prose) exist too."""
    parser = build_parser()
    known = _options(parser)
    for sub in _subcommands(parser).values():
        known |= _options(sub)
        for nested in _subcommands(sub).values():
            known |= _options(nested)
    unknown = {flag for doc in SOURCES
               for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*",
                                      doc.read_text(encoding="utf-8"))} - known
    assert not unknown, sorted(unknown)


def test_the_command_check_sees_the_commands() -> None:
    """The extraction above finds commands in every kind of place, so the check cannot
    pass by finding nothing."""
    shown = [line for doc in SOURCES for line in _commands(doc)]
    for name in ("windows", "screenshot", "capture", "find", "validate", "run", "runs",
                 "pack"):
        assert any(line.startswith(f"j5 {name}") for line in shown), name
    assert any("--allow-exec" in line for line in shown)


def test_open_editor_dry_runs_to_the_end() -> None:
    """A dry ``exec`` stands in for the output, so the steps that use it still check."""
    from j5_macro.runtime.api import RunOptions
    from j5_macro.runtime.interpreter import create_run

    def no_vision() -> object:
        raise ImportError("vision is disabled in this test")

    script_path = SKILL / "examples" / "open_editor.yaml"
    registry = default_registry()
    loaded = load_script(script_path.read_text(encoding="utf-8"), lookup=registry.spec,
                         check=registry.check_params, source_path=str(script_path))
    assert loaded.script is not None
    events: list[tuple[str, dict[str, object]]] = []
    outcome = create_run(loaded.script, registry, lambda kind, payload: events.append(
        (kind, payload)), RunOptions(mode="dry", allow_exec=True, variables={"note": "hi"}),
        vision_factory=no_vision).run()
    assert outcome.status == "completed", outcome.error
    finished = [p["step_id"] for kind, p in events if kind == "step_finished"]
    assert finished[-2:] == ["focus_text", "write"]
