"""``j5 pack`` and ``j5 unpack``, and the real ``j5 validate`` and ``j5 run`` on scripts
with embedded images."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from j5_macro.cli.bundle import image_name
from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV
from j5_macro.script import images as embedded
from png_support import png

ROOT = Path(__file__).resolve().parent.parent
POINTER = ROOT / "examples" / "smoke" / "pointer.yaml"
EMBEDDED = ROOT / "examples" / "smoke" / "pointer_embedded.yaml"

SCRIPT = """\
# Saves the report.
name: Save report
steps:
  - id: open
    click: {image: img/save button.png, threshold: 0.8}   # the toolbar button
  - wait_image_in: img/dialog.png
  - id: again
    drag: {from: {image: ./img/save button.png}, to: {image: img/copy/dialog.png}}
  - if:
      when: {image_visible: "${icon}"}
      then: []
vars: {icon: img/dialog.png}
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A script with its images: two identical files under different paths."""
    (tmp_path / "img" / "copy").mkdir(parents=True)
    (tmp_path / "img" / "save button.png").write_bytes(png(seed=1))
    (tmp_path / "img" / "dialog.png").write_bytes(png(seed=2))
    (tmp_path / "img" / "copy" / "dialog.png").write_bytes(png(seed=3))
    script = tmp_path / "save.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    return script


def test_image_names_come_from_file_stems() -> None:
    assert image_name("save button") == "save_button"
    assert image_name("1st-try") == "_1st-try"
    assert image_name("ñandú") == "and"
    assert image_name("...") == "image"


def test_pack_embeds_every_file_once_and_keeps_the_rest(
        project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "out" / "bundle.yaml"
    bundle.parent.mkdir()
    assert main(["pack", str(project), "-o", str(bundle)]) == 0
    err = capsys.readouterr().err
    assert "embedded 3 images (#save_button, #dialog, #dialog_2), 4 image parameters " \
           "rewritten" in err
    assert "'${icon}' is built at run time" in err
    text = bundle.read_text(encoding="utf-8")
    head, images = text.split("images:\n")
    lines, before = head.splitlines(), SCRIPT.splitlines()
    comment = "# the toolbar button"
    assert lines[4].index(comment) == before[4].index(comment)  # it keeps its column
    assert lines[4].split("#save")[0] == "    click: {image: \""
    expected = (SCRIPT.replace("wait_image_in: img/dialog.png", "wait_image_in: \"#dialog\"")
                .replace("./img/save button.png", "\"#save_button\"")
                .replace("img/copy/dialog.png", "\"#dialog_2\"")).splitlines()
    assert lines[:4] + lines[5:] == expected[:4] + expected[5:]
    assert images.startswith("  save_button:\n    png: |\n")
    # A packed script is valid wherever it is, and its images decode to the files.
    assert main(["validate", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert f"image #save_button (png, 6x4, {len(png(seed=1)):,} bytes)" in out

    # Packing again embeds nothing new.
    assert main(["pack", str(bundle), "-o", str(bundle)]) == 0
    assert "no new image to embed, 0 image parameters rewritten" in capsys.readouterr().err
    assert bundle.read_text(encoding="utf-8") == text


def test_pack_to_stdout_reuses_identical_embedded_images(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = png(seed=2)
    text = SCRIPT.replace("steps:", "steps:\n  - wait_image_in: \"#same\"", 1)
    project.write_text(text + f"images:\n  same: {{png: {embedded.encode_base64(data)}}}\n"
                       .replace("\n}", "}"), encoding="utf-8")
    assert main(["pack", str(project)]) == 0
    out = capsys.readouterr().out
    assert out.count("wait_image_in: \"#same\"\n") == 2  # img/dialog.png has the same bytes
    assert "  dialog:\n" in out and "dialog_2" not in out


@pytest.mark.parametrize(("change", "message"), [
    (lambda p: (p.parent / "img" / "dialog.png").unlink(), "cannot read 'img/dialog.png'"),
    (lambda p: (p.parent / "img" / "dialog.png").write_bytes(b"GIF89a..."),
     "only PNG files can be embedded"),
    (lambda p: (p.parent / "img" / "dialog.png").write_bytes(
        embedded.PNG_SIGNATURE + bytes(embedded.MAX_IMAGE_BYTES)), "per image"),
    (lambda p: p.write_text("steps:\n  - clik: {}\n", encoding="utf-8"), "is not valid"),
])
def test_pack_failures(project: Path, capsys: pytest.CaptureFixture[str], change,
                       message: str) -> None:
    change(project)
    before = project.read_text(encoding="utf-8")
    assert main(["pack", str(project), "-o", str(project)]) == 1
    assert message in capsys.readouterr().err
    assert project.read_text(encoding="utf-8") == before


def test_pack_checks_the_script_total(project: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(embedded, "MAX_SCRIPT_BYTES", 100)
    assert main(["pack", str(project)]) == 1
    assert "over the limit of 100 bytes (8 MiB) per script" in capsys.readouterr().err


def test_unpack_writes_files_back(project: Path, tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "bundle.yaml"
    assert main(["pack", str(project), "-o", str(bundle)]) == 0
    capsys.readouterr()
    out = tmp_path / "unpacked"
    assert main(["unpack", str(bundle), "-d", str(out)]) == 0
    err = capsys.readouterr().err
    assert "the images section is kept" in err  # the ${icon} condition may name one
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == ["bundle.yaml", "img/dialog.png", "img/dialog_2.png",
                     "img/save_button.png"]
    assert (out / "img" / "save_button.png").read_bytes() == png(seed=1)
    text = (out / "bundle.yaml").read_text(encoding="utf-8")
    assert "{image: img/save_button.png, threshold: 0.8}" in text
    assert "to: {image: img/dialog_2.png}" in text
    assert main(["validate", str(out / "bundle.yaml")]) == 0

    # Existing files are left alone without --force.
    capsys.readouterr()
    assert main(["unpack", str(bundle), "-d", str(out)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert main(["unpack", str(bundle), "-d", str(out), "--force"]) == 0


def test_unpack_drops_the_section_and_warns_about_scales(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = embedded.encode_base64(png()).replace("\n", "")
    bundle = tmp_path / "b.yaml"
    bundle.write_text("steps:\n  - id: w\n    wait_image_in: \"#icon\"   # wait\n"
                      f"images:\n  icon: {{png: {data}, scale: 1.25}}\n", encoding="utf-8")
    assert main(["unpack", str(bundle), "-d", str(tmp_path / "out")]) == 0
    assert "capture scale 1.25 cannot be kept in a file" in capsys.readouterr().err
    assert (tmp_path / "out" / "b.yaml").read_text(encoding="utf-8") == (
        "steps:\n  - id: w\n    wait_image_in: img/icon.png # wait\n")


def test_unpack_needs_embedded_images(project: Path, tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["unpack", str(project), "-d", str(tmp_path / "out")]) == 1
    assert "has no embedded images" in capsys.readouterr().err


def test_unpack_needs_a_directory(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["unpack", str(project)])
    assert exit_info.value.code == 2


# --- the real engine ----------------------------------------------------------------------


def test_the_embedded_example_is_the_packed_pointer_example(tmp_path: Path) -> None:
    packed = tmp_path / "pointer.yaml"
    assert main(["pack", str(POINTER), "-o", str(packed)]) == 0
    ours = packed.read_text(encoding="utf-8").splitlines()
    example = EMBEDDED.read_text(encoding="utf-8").splitlines()
    # The same but for the header comment and the name.
    assert ours[ours.index("input:"):] == example[example.index("input:"):]


def test_validate_reports_image_problems_with_their_line(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "bad.yaml"
    script.write_text("steps:\n  - id: c\n    click: {image: \"#nope\"}\n"
                      "images:\n  icon: {png: 'not*base64'}\n", encoding="utf-8")
    assert main(["validate", str(script)]) == 1
    out = capsys.readouterr().out
    assert (f"{script}:2: error[validation_failed]: image: no embedded image named 'nope'; "
            "the script has icon (step c)") in out
    assert (f"{script}:5: error[validation_failed]: image 'icon': 'png' is not valid "
            "base64") in out


def test_a_copied_embedded_script_runs_from_anywhere(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "elsewhere" / "pointer_embedded.yaml"
    copy.parent.mkdir()
    shutil.copy(EMBEDDED, copy)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--json", str(copy)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finished = [e for e in events if e["type"] == "run_finished"]
    assert finished[0]["payload"]["status"] == "completed"
    failures = [e for e in events if e["type"] == "step_failed"]
    assert not failures
    templates = {e["payload"]["template"] for e in events if e["type"] == "image_search"}
    assert templates <= {"#pattern"}


def test_pack_embeds_anchors_and_hovered_looks(
        project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project.write_text("""\
steps:
  - wait_image_in: img/dialog.png
    as: dialog
  - click: {image: img/save button.png, after: img/dialog.png,
            hover: {image: img/copy/dialog.png}}
  - click: {image: img/save button.png, before: "${dialog}"}
""", encoding="utf-8")
    bundle = tmp_path / "bundle.yaml"
    assert main(["pack", str(project), "-o", str(bundle)]) == 0
    assert "5 image parameters rewritten" in capsys.readouterr().err
    text = bundle.read_text(encoding="utf-8")
    assert 'after: "#dialog"' in text and 'hover: {image: "#dialog_2"}' in text
    assert 'before: "${dialog}"' in text
    assert main(["validate", str(bundle)]) == 0
