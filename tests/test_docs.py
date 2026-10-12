"""The documentation stays consistent: links and anchors resolve, and complete scripts
shown in the docs validate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from j5_macro.script import load_script
from j5_macro.steps import default_registry

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([ROOT / "README.md", ROOT / "editors" / "vscode" / "README.md",
               *(ROOT / "docs").glob("*.md")])
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")


def strip_code(text: str) -> str:
    """The text without fenced code blocks and inline code (links there are examples)."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if fenced or not match:
            continue
        base = slug(match.group(1))
        n = counts.get(base, 0)
        counts[base] = n + 1
        found.add(base if n == 0 else f"{base}-{n}")
    return found


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_and_anchors_resolve(doc: Path) -> None:
    problems = []
    for target in LINK.findall(strip_code(doc.read_text(encoding="utf-8"))):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # https:, mailto:...
            continue
        file_part, _, anchor = target.partition("#")
        path = (doc.parent / file_part).resolve() if file_part else doc
        if not path.exists():
            problems.append(f"{target}: {path} does not exist")
            continue
        if anchor and path.suffix == ".md" and anchor not in anchors(path):
            problems.append(f"{target}: no heading #{anchor} in {path.name}")
    assert not problems, "\n".join(problems)


def test_every_doc_is_listed_in_the_index() -> None:
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    missing = [p.name for p in (ROOT / "docs").glob("*.md")
               if p.name != "index.md" and f"({p.name})" not in index]
    assert not missing


def complete_scripts() -> list[tuple[str, str]]:
    """YAML blocks of the docs that are whole scripts (they have ``version`` and
    ``steps`` at the top level)."""
    found = []
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for i, block in enumerate(re.findall(r"```yaml\n(.*?)```", text, re.S)):
            if re.search(r"^version:", block, re.M) and re.search(r"^steps:", block, re.M):
                found.append((f"{doc.relative_to(ROOT)}[{i}]", block))
    return found


@pytest.mark.parametrize(("where", "block"), complete_scripts(), ids=lambda v: v
                         if isinstance(v, str) and "[" in v else "")
def test_complete_scripts_in_the_docs_validate(where: str, block: str) -> None:
    registry = default_registry()
    result = load_script(block, lookup=registry.spec, check=registry.check_params)
    assert result.ok, [e.message for e in result.errors]


@pytest.mark.parametrize("script", sorted((ROOT / "examples").glob("*.yaml")),
                         ids=lambda p: p.name)
def test_examples_validate(script: Path) -> None:
    registry = default_registry()
    result = load_script(script.read_text(encoding="utf-8"), lookup=registry.spec,
                         check=registry.check_params, source_path=str(script))
    assert result.ok, [e.message for e in result.errors]
