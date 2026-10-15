"""``j5 capture``, ``pick``, ``find``, ``screenshot`` and ``windows``: ready-to-paste
YAML, argument checks, and the engine's answers rendered (the screen itself is tested
in tests/backends/test_e2e_authoring.py and tests/engine/test_engine_authoring.py)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from ruamel.yaml import YAML

from j5_macro.cli import authoring
from j5_macro.cli.commands import CliError, ExitCode, Output
from j5_macro.cli.main import main


@pytest.mark.parametrize(("params", "text"), [
    ({"image": "img/save.png"}, "click: {image: img/save.png}"),
    ({"image": "#save_button"}, 'click: {image: "#save_button"}'),
    ({"window": {"title": "Notes"}, "x": 212, "y": 148},
     "click: {window: {title: Notes}, x: 212, y: 148}"),
    ({"window": {"title": "Untitled: Notes"}, "x": 1, "y": 2},
     'click: {window: {title: "Untitled: Notes"}, x: 1, y: 2}'),
    ({"image": "a.png", "region": [0, 10, 200, 100], "threshold": 0.85, "scale": 1.5},
     "click: {image: a.png, region: [0, 10, 200, 100], threshold: 0.85, scale: 1.5}"),
    ({"window": {"title": "true"}, "x": 0, "y": 0},
     'click: {window: {title: "true"}, x: 0, y: 0}'),
    ({"window": {"title": "123"}, "x": 0, "y": 0},
     'click: {window: {title: "123"}, x: 0, y: 0}'),
    ({"window": {"title": "Résumé - Writer"}, "x": 0, "y": 0},
     'click: {window: {title: "Résumé - Writer"}, x: 0, y: 0}'),
])
def test_step_yaml_reads_back_as_written(params: dict[str, Any], text: str) -> None:
    line = authoring.step_yaml("click", params)
    assert line == text
    assert YAML(typ="safe").load(line) == {"click": params}


def test_parse_rect() -> None:
    assert authoring.parse_rect("10,20,30,40") == [10, 20, 30, 40]
    assert authoring.parse_rect(" -5, 0 7 8") == [-5, 0, 7, 8]
    for bad in ("1,2,3", "1,2,3,x", "1,2,0,4", "1.5,2,3,4"):
        with pytest.raises(ValueError):
            authoring.parse_rect(bad)


@pytest.mark.parametrize("args", [
    ["capture"],
    ["capture", "--name", "a", "-o", "a.png"],
    ["capture", "--embed", "s.yaml"],
    ["capture", "-o", "a.png", "--padding", "-1"],
    ["find", "#icon"],
    ["find", "a.png", "--threshold", "2"],
    ["find", "a.png", "--scale", "0"],
])
def test_usage_errors_exit_with_2(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(args) == ExitCode.USAGE
    assert capsys.readouterr().err.startswith("j5: ")


def test_a_bad_rectangle_is_an_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["capture", "-o", "a.png", "--bbox", "1,2,3"])
    assert info.value.code == ExitCode.USAGE
    assert "x,y,width,height" in capsys.readouterr().err


class FakeClient:
    """Answers ``call`` from a table; raises the EngineError it is given."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def call(self, type: str, payload: dict[str, Any] | None = None, *,
                   timeout: float | None = 30.0) -> dict[str, Any]:
        self.calls.append((type, dict(payload or {}), timeout))
        answer = self.answers[type]
        if isinstance(answer, Exception):
            raise answer
        result: dict[str, Any] = answer
        return result


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> Any:
    import contextlib

    holder: dict[str, FakeClient] = {}

    @contextlib.asynccontextmanager
    async def engine(cmd: Any, io: Any = None) -> Any:
        yield holder["client"]

    monkeypatch.setattr(authoring, "engine", engine)

    def install(answers: dict[str, Any]) -> FakeClient:
        holder["client"] = FakeClient(answers)
        return holder["client"]

    return install


def output() -> tuple[Output, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return Output(out=out, err=err), out, err


def run(coro: Any) -> int:
    import asyncio

    result: int = asyncio.run(coro)
    return result


def test_pick_prints_a_window_relative_click(fake_engine: Any) -> None:
    client = fake_engine({"pick_point": {
        "x": 312, "y": 228, "display_scale": 1.0, "window": "Notes",
        "window_area": [100, 80, 400, 300], "window_point": [212, 148]}})
    io_, out, err = output()
    assert run(authoring.pick(["engine"], window=None, as_json=False, io=io_)) == 0
    assert out.getvalue() == "click: {window: {title: Notes}, x: 212, y: 148}\n"
    assert client.calls == [("pick_point", {}, None)]  # no timeout: the user decides
    assert "(212, 148) in 'Notes'" in err.getvalue()


def test_pick_without_a_window_prints_desktop_coordinates(fake_engine: Any) -> None:
    fake_engine({"pick_point": {"x": 5, "y": 6, "display_scale": 1.0}})
    io_, out, _ = output()
    run(authoring.pick(["engine"], window=None, as_json=False, io=io_))
    assert out.getvalue() == "click: {x: 5, y: 6}\n"


def test_cancelled_exits_with_3(fake_engine: Any) -> None:
    from j5_macro.client import EngineError
    from j5_macro.protocol import ErrorInfo

    fake_engine({"pick_point": EngineError("pick_point", ErrorInfo(
        code="invalid_state", message="cancelled by the user",
        details={"reason": "cancelled"}))})
    io_, _, _ = output()
    with pytest.raises(CliError) as info:
        run(authoring.pick(["engine"], window=None, as_json=False, io=io_))
    assert info.value.exit_code == ExitCode.STOPPED


def test_capture_to_a_file_hints_at_the_display_scale(fake_engine: Any) -> None:
    client = fake_engine({"capture_region": {
        "bbox": [10, 20, 30, 40], "display_scale": 1.5, "path": "/w/img/save.png",
        "warnings": [{"code": "flat", "message": "flat colour"}]}})
    io_, out, err = output()
    options = authoring.CaptureOptions(output="img/save.png", bbox=[10, 20, 30, 40])
    assert run(authoring.capture(["engine"], options, io=io_)) == 0
    assert out.getvalue() == "click: {image: img/save.png}\n"
    notes = err.getvalue()
    assert "warning: flat colour" in notes and "capture_scale: 1.5" in notes
    (call,) = client.calls
    assert call[0] == "capture_region" and call[2] == 60.0
    assert call[1]["bbox"] == [10, 20, 30, 40] and call[1]["path"].endswith("save.png")


def test_find_prints_a_click_when_found_and_exits_1_when_not(fake_engine: Any) -> None:
    fake_engine({"find_image": {
        "found": True, "score": 0.998, "bbox": [1, 2, 3, 4], "center": [2, 4],
        "region": [0, 0, 100, 100], "scale": 1.0, "display_scale": 1.0,
        "capture_scale": 1.0, "elapsed_ms": 3}})
    io_, out, err = output()
    options = authoring.FindOptions(window="Notes", threshold=0.8)
    assert run(authoring.find(["engine"], "img/a.png", options, io=io_)) == 0
    assert out.getvalue() == \
        "click: {image: img/a.png, window: {title: Notes}, threshold: 0.8}\n"
    fake_engine({"find_image": {
        "found": False, "score": 0.5, "bbox": [1, 2, 3, 4], "region": [0, 0, 100, 100],
        "scale": 1.0, "display_scale": 1.0, "capture_scale": 1.0, "elapsed_ms": 3}})
    io_, out, err = output()
    assert run(authoring.find(["engine"], "img/a.png", authoring.FindOptions(),
                              io=io_)) == ExitCode.FAILED
    assert out.getvalue() == ""
    assert "not found: best score 0.500" in err.getvalue()


def test_find_all_lists_every_match_in_reading_order(fake_engine: Any) -> None:
    client = fake_engine({"find_image": {
        "found": True, "score": 0.998, "bbox": [10, 20, 30, 10], "center": [25, 25],
        "region": [0, 0, 100, 100], "scale": 1.0, "display_scale": 1.0,
        "capture_scale": 1.0, "elapsed_ms": 3, "window": "Notes",
        "window_bbox": [10, 0, 30, 10],
        "matches": [
            {"score": 0.998, "bbox": [10, 20, 30, 10], "center": [25, 25],
             "window_bbox": [10, 0, 30, 10]},
            {"score": 0.951, "bbox": [10, 60, 30, 10], "center": [25, 65],
             "window_bbox": [10, 40, 30, 10]}]}})
    io_, out, err = output()
    options = authoring.FindOptions(window="Notes", all=True)
    assert run(authoring.find(["engine"], "img/a.png", options, io=io_)) == 0
    assert client.calls[0][1]["all"] is True
    assert out.getvalue() == "click: {image: img/a.png, window: {title: Notes}}\n"
    notes = err.getvalue()
    assert "2 matches of img/a.png, in reading order" in notes
    assert "  1. [10, 20, 30, 10] ([10, 0, 30, 10] in 'Notes'), score 0.998" in notes
    assert "  2. [10, 60, 30, 10] ([10, 40, 30, 10] in 'Notes'), score 0.951" in notes
    assert "choose another with pick, nth" in notes


def test_windows_and_screenshot_text(fake_engine: Any) -> None:
    fake_engine({"list_windows": {"windows": [{"title": "Notes", "bbox": [0, 30, 800, 570]}]},
                 "screenshot": {"path": "/tmp/a.png", "bbox": [0, 30, 800, 570],
                                "display_scale": 1.0, "window": "Notes",
                                "window_bbox": [0, 0, 800, 570]}})
    io_, out, _ = output()
    run(authoring.windows(["engine"], as_json=False, io=io_))
    assert out.getvalue() == "[0, 30, 800, 570]        Notes\n"
    io_, out, _err = output()
    run(authoring.screenshot(["engine"], output=None, window="Notes", region=None,
                             as_json=True, io=io_))
    assert json.loads(out.getvalue())["path"] == "/tmp/a.png"
