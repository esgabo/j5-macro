"""The script editor against a real engine with the fake input backend: the palette,
the step form, drag and drop, the header form, images, and saving as one file."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QPushButton, QWidget

from j5_macro.gui.models.steps import find_step, iter_steps
from j5_macro.gui.views import fields as field_views
from j5_macro.gui.views.fields import FieldContext
from j5_macro.script.loader import load_file
from j5_macro.steps import default_registry


def png(width: int, height: int, seed: int = 0) -> bytes:
    """A small RGB PNG with varied pixels (as tests/png_support.py makes them)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = b"".join(
        b"\0" + bytes(v for x in range(width) for v in (
            (x * 40 + seed * 7) % 256, (y * 60 + seed * 13) % 256, (x * y * 25 + seed) % 256))
        for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


COMMENTED = """\
# A script with comments: they must survive every edit.
version: 1
name: Commented   # the name
steps:
  # Say hello first.
  - id: hello
    log: "hello"   # greeting
  - id: tap
    click: {x: 10, y: 20}
  - id: repeat
    loop:
      times: 2
      do:
        - id: nap
          sleep: 1s
  - id: press
    key: ctrl+s
# The end.
"""


@pytest.fixture
def editor_window(qtbot: Any, window: Any, scripts: Path) -> Any:
    window.editor_path = scripts / "commented.yaml"
    window.editor_path.write_text(COMMENTED, encoding="utf-8", newline="\n")
    open_script(qtbot, window, window.editor_path)
    wait_for(qtbot, lambda: len(window.editor.registry) > 0)
    return window


def open_script(qtbot: Any, window: Any, path: Path) -> Any:
    window.open_target(str(path))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.path is not None
             and os.path.samefile(window.ctx.document.state.path, path))
    return window.ctx.document.state


def ids(window: Any) -> list[str]:
    state = window.ctx.document.state
    return [step.id for step in iter_steps(state.steps)] if state is not None else []


def top_ids(window: Any) -> list[str]:
    return [step.id for step in window.ctx.document.state.steps]


def disk(window: Any) -> str:
    return str(window.editor_path.read_text(encoding="utf-8"))


def wait_disk(qtbot: Any, window: Any, condition: Any) -> str:
    wait_for(qtbot, lambda: condition(disk(window)))
    wait_for(qtbot, lambda: window.ctx.document.state.source == disk(window)
             and window.editor.queue.idle)
    return disk(window)


def type_into(qtbot: Any, edit: Any, text: str) -> None:
    edit.setFocus()
    edit.selectAll()
    qtbot.keyClicks(edit, text)


def test_the_palette_lists_the_registry_and_inserts_after_the_selection(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    palette = window.editor.step_palette
    assert palette.type_names() == [
        "click", "move", "drag", "scroll", "type", "key", "wait_image_in",
        "wait_image_out", "wait_window_in", "wait_window_out", "sleep", "if", "loop",
        "pause", "set", "log", "exec"]
    item = palette.item_for("click")
    assert item is not None and "Click on an image" in item.toolTip(0)

    window.editor.tree.select_step("hello")
    palette.insert_requested.emit("wait_window_in")
    text = wait_disk(qtbot, window, lambda t: "wait_window_in" in t)
    assert top_ids(window)[:2] == ["hello", "wait_window_in_1"]
    assert "    wait_window_in: {title: Untitled}\n" in text
    assert text.startswith("# A script with comments")
    assert "  # Say hello first.\n" in text and text.endswith("# The end.\n")
    wait_for(qtbot, lambda: window.editor.tree.selected_step_id() == "wait_window_in_1")
    assert window.editor.form.step is not None
    assert window.editor.form.step.id == "wait_window_in_1"

    # Into the selected block row: an empty 'else' is created.
    palette.insert_requested.emit("if")
    wait_disk(qtbot, window, lambda t: "if_1" in t)
    window.editor.tree.select_block("if_1", "else")
    assert window.editor.tree.selected_block() == ("if_1", "else")
    palette.insert_requested.emit("log")
    wait_disk(qtbot, window, lambda t: "log_2" in t)
    if_step = find_step(window.ctx.document.state.steps, "if_1")
    assert [s.id for s in if_step.blocks["else"]] == ["log_2"]


def test_editing_a_param_in_the_form_changes_only_its_line(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    window.editor.tree.select_step("hello")
    form = window.editor.form
    message = form.field("message")
    assert message is not None and message.edit.text() == "hello"
    type_into(qtbot, message.edit, "hello again")
    assert form.pending()
    text = wait_disk(qtbot, window, lambda t: "hello again" in t)
    assert text == COMMENTED.replace('log: "hello"   # greeting',
                                     'log: "hello again" # greeting')  # same column

    # The click's target: the position fields and a choice sent at once.
    window.editor.tree.select_step("tap")
    target = form.field("x")
    assert target is not None and target.target.current_mode() == "point"
    type_into(qtbot, target.target.point.x_edit, "15")
    text = wait_disk(qtbot, window, lambda t: "x: 15" in t)
    assert "    click: {x: 15, y: 20}\n" in text
    button = form.field("button")
    button.combo.setCurrentIndex(button.combo.findData("right"))
    button.combo.activated.emit(button.combo.currentIndex())
    text = wait_disk(qtbot, window, lambda t: "button: right" in t)
    assert "    click: {x: 15, y: 20, button: right}\n" in text


def test_common_fields_rename_and_options(qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    form = window.editor.form
    window.editor.tree.select_step("nap")
    duration = form.field("duration")
    type_into(qtbot, duration.edit, "2s±10%")
    timeout = form.field("timeout", common=True)
    type_into(qtbot, timeout.edit, "5s")
    keep = form.field("as", common=True)
    type_into(qtbot, keep.name, "slept")
    keep.secret.setChecked(True)  # sent at once, with what was typed before
    text = wait_disk(qtbot, window, lambda t: "secret: true" in t)
    assert "          sleep: 2s±10%\n" in text
    assert "          timeout: 5s\n" in text
    assert "          as: {name: slept, secret: true}\n" in text

    step_id = form.field("id", common=True)
    type_into(qtbot, step_id.edit, "rest")
    form.flush()
    wait_disk(qtbot, window, lambda t: "id: rest" in t)
    wait_for(qtbot, lambda: window.editor.tree.selected_step_id() == "rest")
    assert "nap" not in ids(window)


def test_invalid_values_show_problems_and_are_not_sent(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    form = window.editor.form
    window.editor.tree.select_step("tap")
    target = form.field("x")
    before = disk(window)
    type_into(qtbot, target.target.point.x_edit, "abc")
    assert form.problem_text("x") == "x: expected a whole number"
    form.flush()
    qtbot.wait(300)
    assert disk(window) == before

    # A value only the engine refuses: saved, then shown under the field and in the tree.
    window.editor.tree.select_step("hello")
    window.editor.step_palette.insert_requested.emit("wait_window_in")
    wait_disk(qtbot, window, lambda t: "wait_window_in_1" in t)
    wait_for(qtbot, lambda: window.editor.tree.selected_step_id() == "wait_window_in_1")
    title = form.field("title")
    type_into(qtbot, title.edit, "")
    title.edit.setText("")
    title.edit.textEdited.emit("")
    form.flush()
    wait_for(qtbot, lambda: not window.ctx.document.state.valid)
    wait_for(qtbot, lambda: "must not be empty" in form.problem_text("title"))
    assert window.editor.tree.problem_steps() == ["wait_window_in_1"]
    assert "must not be empty" in window.editor.problems.text()
    # Fixing it clears the problems.
    type_into(qtbot, title.edit, "Editor")
    wait_for(qtbot, lambda: window.ctx.document.state.valid)
    wait_for(qtbot, lambda: not form.problem_text("title"))
    assert window.editor.tree.problem_steps() == []


def drop_on(tree: Any, mime: Any, item: Any, fraction: float) -> None:
    rect = tree.visualItemRect(item)
    point = QPoint(rect.center().x(), int(rect.top() + rect.height() * fraction))
    event = QDropEvent(point.toPointF(), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tree.dropEvent(event)


def test_drag_and_drop_reorders_and_moves_across_blocks(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    tree = window.editor.tree
    # 'press' dropped on the top edge of 'hello': it goes first.
    drop_on(tree, tree.mimeData([tree.item_for("press")]), tree.item_for("hello"), 0.1)
    wait_disk(qtbot, window, lambda t: t.index("id: press") < t.index("id: hello"))
    assert top_ids(window) == ["press", "hello", "tap", "repeat"]
    # 'hello' dropped in the middle of the loop: into its 'do' block.
    drop_on(tree, tree.mimeData([tree.item_for("hello")]), tree.item_for("repeat"), 0.5)
    wait_disk(qtbot, window, lambda t: t.index("id: hello") > t.index("id: repeat"))
    repeat = find_step(window.ctx.document.state.steps, "repeat")
    assert [s.id for s in repeat.blocks["do"]] == ["nap", "hello"]
    # Out of the block, onto the bottom edge of 'tap'.
    drop_on(tree, tree.mimeData([tree.item_for("nap")]), tree.item_for("tap"), 0.9)
    wait_disk(qtbot, window, lambda t: t.index("id: nap") < t.index("id: repeat"))
    assert top_ids(window) == ["press", "tap", "nap", "repeat"]
    text = disk(window)
    assert "  # Say hello first.\n" in text and "# The end." in text
    # A step type from the palette dropped on a block row is inserted there.
    palette = window.editor.step_palette
    mime = palette.tree.mimeData([palette.item_for("key")])
    drop_on(tree, mime, tree.block_item("repeat", "do"), 0.5)
    wait_disk(qtbot, window, lambda t: "key_2" in t)
    repeat = find_step(window.ctx.document.state.steps, "repeat")
    assert [s.id for s in repeat.blocks["do"]] == ["hello", "key_2"]
    # A block dropped into itself changes nothing.
    before = disk(window)
    drop_on(tree, tree.mimeData([tree.item_for("repeat")]), tree.item_for("hello"), 0.9)
    qtbot.wait(300)
    assert disk(window) == before


def test_toolbar_actions(qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    editor = window.editor
    editor.tree.select_step("tap")
    editor.up_action.trigger()
    wait_disk(qtbot, window, lambda t: t.index("id: tap") < t.index("id: hello"))
    assert top_ids(window)[:2] == ["tap", "hello"]
    assert editor.tree.selected_step_id() == "tap"
    editor.down_action.trigger()
    wait_disk(qtbot, window, lambda t: t.index("id: tap") > t.index("id: hello"))
    editor.tree.select_step("repeat")
    editor.duplicate_action.trigger()
    wait_disk(qtbot, window, lambda t: "loop_2" in t)
    assert top_ids(window) == ["hello", "tap", "repeat", "loop_2", "press"]
    wait_for(qtbot, lambda: editor.tree.selected_step_id() == "loop_2")
    editor.delete_action.trigger()
    wait_disk(qtbot, window, lambda t: "loop_2" not in t)
    assert top_ids(window) == ["hello", "tap", "repeat", "press"]
    wait_for(qtbot, lambda: editor.tree.selected_step_id() == "press")


def test_the_yaml_view_follows_the_selection_both_ways(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    editor = window.editor
    editor.tree.select_step("repeat")
    assert editor.yaml_view.highlighted_line() == 10
    editor.yaml_view.line_clicked.emit(15)  # inside 'nap'
    assert editor.tree.selected_step_id() == "nap"
    assert editor.yaml_view.highlighted_line() == 14


def test_header_form(qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    header = window.editor.header
    window.editor.tabs.setCurrentWidget(header)
    assert header.name.text() == "Commented"
    header.add_param()
    text = wait_disk(qtbot, window, lambda t: "params:" in t)
    assert "params:\n  param:\nsteps:" in text
    assert [p["name"] for p in window.ctx.document.state.params] == ["param"]
    # Not required: with a default.
    header.param_checkbox(0, "required").setChecked(False)
    text = wait_disk(qtbot, window, lambda t: "default:" in t)
    assert "  param: {default: ''}\n" in text
    header.add_var()
    text = wait_disk(qtbot, window, lambda t: "vars:" in t)
    assert "vars:\n  var: 0\n" in text
    header.vars.item(0, 1).setText("[1, 2]")
    header.flush()
    text = wait_disk(qtbot, window, lambda t: "var: [1, 2]" in t)
    type_into(qtbot, header.name, "Renamed")
    text = wait_disk(qtbot, window, lambda t: "name: Renamed" in t)
    assert "name: Renamed     # the name\n" in text  # the comment keeps its column
    strategy = header.profile.inputs[("mouse", "strategy")]
    strategy.setCurrentIndex(strategy.findData("direct"))
    strategy.activated.emit(strategy.currentIndex())
    text = wait_disk(qtbot, window, lambda t: "input:" in t)
    assert "input:\n  mouse: {strategy: direct}\n" in text
    assert window.ctx.document.state.input == {"mouse": {"strategy": "direct"}}


def test_embedded_images_add_rename_delete(qtbot: Any, editor_window: Any,
                                           tmp_path: Path) -> None:
    window = editor_window
    header = window.editor.header
    image = tmp_path / "save button.png"
    image.write_bytes(png(8, 6, seed=4))
    assert header.add_image_file(str(image)) == "save_button"
    text = wait_disk(qtbot, window, lambda t: "images:" in t)
    assert "images:\n  save_button:\n    png: |\n" in text
    wait_for(qtbot, lambda: header.image_names() == ["save_button"])
    assert "8 x 6 px" in header.images.item(0, 1).text()

    # The click aims at it, then the image is renamed: the reference follows.
    window.editor.tree.select_step("tap")
    target = window.editor.form.field("image")
    combo = target.target.image.combo
    assert combo.findText("#save_button") >= 0
    target.target.mode.setCurrentIndex(0)  # Image
    target.target._sync()
    target.target.image.set_and_send("#save_button")
    text = wait_disk(qtbot, window, lambda t: "#save_button" in t)
    assert "    click: {image: '#save_button'}\n" in text
    assert header.image_references("save_button") == ["tap"]
    header.rename_image("save_button", "ok")
    text = wait_disk(qtbot, window, lambda t: '"#ok"' in t)
    assert "  ok:\n" in text and "save_button" not in text
    header.delete_image("ok")
    wait_disk(qtbot, window, lambda t: "images:" not in t)
    wait_for(qtbot, lambda: not window.ctx.document.state.valid)  # '#ok' is gone

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"GIF89a not a png")
    assert header.add_image_file(str(bad)) is None
    assert "only PNG files can be embedded" in header.problems.text()


def test_save_as_a_self_contained_file_and_unpack(qtbot: Any, editor_window: Any,
                                                  scripts: Path) -> None:
    window = editor_window
    (scripts / "img").mkdir()
    (scripts / "img" / "menu.png").write_bytes(png(10, 8, seed=1))
    window.editor.tree.select_step("tap")
    target = window.editor.form.field("image")
    target.target.mode.setCurrentIndex(0)
    target.target._sync()
    target.target.image.use_file(str(scripts / "img" / "menu.png"))
    wait_disk(qtbot, window, lambda t: "img/menu.png" in t)

    packed = scripts / "packed.yaml"
    window.editor.save_packed(str(packed))
    wait_for(qtbot, lambda: packed.exists())
    assert "embedded 1 image(s) (#menu)" in window.editor.last_message
    text = packed.read_text(encoding="utf-8")
    assert '    click: {image: "#menu"}\n' in text and "# The end." in text
    result = load_file(str(packed), lookup=default_registry().spec,
                       check=default_registry().check_params)
    assert result.script is not None and list(result.script.images) == ["menu"]
    assert window.ctx.document.state.path == str(window.editor_path)  # still open

    # Unpacking the packed copy into another folder opens the result.
    open_script(qtbot, window, packed)
    out = scripts / "out"
    out.mkdir()
    window.editor.unpack_to(str(out))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.path == str(out / "packed.yaml"))
    assert (out / "img" / "menu.png").read_bytes() == png(10, 8, seed=1)
    assert "click: {image: img/menu.png}" in (out / "packed.yaml").read_text(
        encoding="utf-8")


def test_field_tools_are_added_to_image_and_point_fields(
        qtbot: Any, editor_window: Any) -> None:
    window = editor_window
    contexts: list[FieldContext] = []

    def tools(context: FieldContext) -> list[QWidget]:
        contexts.append(context)
        if context.kind != "point":
            return []
        button = QPushButton("Pick")
        button.clicked.connect(lambda: context.set_value({"x": 300, "y": 400}))
        return [button]

    unregister = field_views.register_field_tools(tools)
    try:
        window.editor.tree.select_step("tap")
        kinds = {(c.kind, c.param) for c in contexts}
        assert {("image", "image"), ("point", "x/y"), ("region", "region"),
                ("window", "window")} <= kinds
        point = next(c for c in contexts if c.kind == "point")
        assert (point.ctx, point.step_id, point.step_type) == (window.ctx, "tap", "click")
        assert point.get_value() == {"x": 10, "y": 20}
        assert point.step_params()["x"] == 10
        buttons = [b for b in window.editor.form.findChildren(QPushButton)
                   if b.text() == "Pick"]
        assert len(buttons) == 1
        buttons[0].click()
        text = wait_disk(qtbot, window, lambda t: "x: 300" in t)
        assert "    click: {x: 300, y: 400}\n" in text
    finally:
        unregister()
    assert field_views.field_tools(contexts[0]) == []


def test_new_script_and_engine_restart_keep_editing(qtbot: Any, window: Any,
                                                    scripts: Path) -> None:
    window.open_target(str(scripts))
    wait_for(qtbot, lambda: window.script_list.folder is not None)
    window.script_list.create_script(str(scripts / "fresh.yaml"), "Fresh")
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.name == "Fresh")
    wait_for(qtbot, lambda: len(window.editor.registry) > 0)
    window.editor.step_palette.insert_requested.emit("log")
    wait_for(qtbot, lambda: "log_1" in (scripts / "fresh.yaml").read_text(encoding="utf-8"))
    old_id = window.ctx.document.script_id
    with qtbot.waitSignal(window.ctx.bridge.engine_started, timeout=WAIT):
        window.ctx.bridge.restart()
    # Script ids belong to the engine: the document opens the file again.
    wait_for(qtbot, lambda: window.ctx.document.script_id not in (None, old_id)
             and window.ctx.document.state is not None
             and window.ctx.document.state.script_id == window.ctx.document.script_id)
    window.editor.tree.select_step("log_1")
    window.editor.duplicate_action.trigger()
    wait_for(qtbot, lambda: "log_2" in (scripts / "fresh.yaml").read_text(encoding="utf-8"))
