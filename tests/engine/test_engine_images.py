"""Embedded images through the engine: ``load`` and ``get_script`` describe them, an
unknown reference fails ``load``, and ``set_meta`` writes them."""

from __future__ import annotations

import base64
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest
from engine_harness import Engine, FakeFactory

from j5_macro.engine.scripts import script_version
from j5_macro.protocol.payloads import GetScriptResult
from j5_macro.script import images as embedded

HEADER = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 32, 16)
          + b"\x08\x02\x00\x00\x00")
"""The start of a 32x16 PNG; enough with the loader's pixel check turned off."""
DATA = base64.b64encode(HEADER).decode()

SCRIPT = f"""\
name: Embedded
steps:
  - id: wait
    wait_image_in: "#icon"
images:
  icon: {{png: {DATA}, scale: 1.5}}
"""


@pytest.fixture(autouse=True)
def no_pixel_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedded, "pixel_check", lambda: None)


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def engine(factory: FakeFactory) -> Iterator[Engine]:
    eng = Engine(run_factory=factory)
    yield eng
    eng.close()


ICON = {"name": "icon", "format": "png", "bytes": len(HEADER), "width": 32, "height": 16,
        "scale": 1.5}


def test_load_and_get_script_describe_the_images_without_their_data(engine: Engine) -> None:
    loaded = engine.ok("load", {"source": SCRIPT})
    assert loaded["images"] == [ICON]
    script = GetScriptResult.from_json(engine.ok("get_script",
                                                 {"script_id": loaded["script_id"]}))
    assert script.images == [ICON]
    assert script.source == SCRIPT  # the section itself is in the text


def test_load_and_get_script_report_the_capture_scale(engine: Engine) -> None:
    loaded = engine.ok("load", {"source": SCRIPT})
    assert "capture_scale" not in loaded
    loaded = engine.ok("load", {"source": "capture_scale: 1.25\n" + SCRIPT})
    assert loaded["capture_scale"] == 1.25
    script = GetScriptResult.from_json(engine.ok("get_script",
                                                 {"script_id": loaded["script_id"]}))
    assert script.capture_scale == 1.25


def test_an_unknown_reference_fails_load_with_the_step_and_parameter(engine: Engine) -> None:
    error = engine.error("load", {"source": SCRIPT.replace('"#icon"', '"#nope"')})
    assert error["code"] == "validation_failed"
    [problem] = error["details"]["errors"]
    assert problem["step_id"] == "wait" and problem["line"] == 3
    assert problem["details"] == {"param": "image", "image": "nope"}


def test_set_meta_writes_images_and_runs_get_them(engine: Engine, factory: FakeFactory,
                                                   tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    text = "name: S\nsteps:\n  - id: wait\n    wait_image_in: img/icon.png\n"
    path.write_text(text, encoding="utf-8")
    script_id = engine.ok("load", {"path": str(path)})["script_id"]
    response = engine.call("edit_script", {
        "script_id": script_id, "base_version": script_version(text), "edits": [
            {"op": "set_meta", "images": {"icon": {"png": DATA}}},
            {"op": "update", "step_id": "wait", "params": {"image": "#icon"}},
        ]})
    assert response["ok"] is True, response
    saved = path.read_text(encoding="utf-8")
    assert saved == (f"name: S\nsteps:\n  - id: wait\n    wait_image_in: '#icon'\n"
                     f"images:\n  icon:\n    png: |\n      {DATA}\n")
    script = GetScriptResult.from_json(engine.ok("get_script", {"script_id": script_id}))
    assert [image["name"] for image in script.images] == ["icon"]

    engine.wait_finished(engine.start(script_id))
    assert factory.last.script.images["icon"].data == HEADER
