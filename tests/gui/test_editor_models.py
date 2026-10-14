"""The editor's pure logic, without Qt: the registry and palette, field kinds and value
parsing, the header values, and the edits behind insert, move, drag and drop, delete
and duplicate (checked by applying them with the engine's own structured editor)."""

from __future__ import annotations

from typing import Any

import pytest

from j5_macro.engine.scripts import step_node
from j5_macro.gui.models import edits
from j5_macro.gui.models.edits import Target
from j5_macro.gui.models.fields import (
    field_kind,
    format_literal,
    has_target,
    parse_duration_text,
    parse_integer,
    parse_literal,
    parse_number,
    parse_offset,
    parse_rect,
    parse_scalar_text,
    problem_field,
    problems_by_step,
    region_mode,
    step_problems,
)
from j5_macro.gui.models.header import (
    MOUSE_SETTINGS,
    ParamRow,
    image_summary,
    params_value,
    parse_default,
    parse_setting,
    var_rows,
    vars_value,
)
from j5_macro.gui.models.registry import (
    PLUGINS_GROUP,
    ParamInfo,
    StepRegistry,
    StepType,
    new_step,
    palette_groups,
)
from j5_macro.gui.models.steps import StepItem, parse_steps, step_summary
from j5_macro.protocol.payloads import EditScriptPayload
from j5_macro.script.editor import apply_edits
from j5_macro.script.loader import load_script
from j5_macro.steps import default_registry

ENGINE_REGISTRY = default_registry()
REGISTRY = StepRegistry.from_result({"step_types": ENGINE_REGISTRY.describe()})

SCRIPT = """\
version: 1
name: Edits
steps:
  - id: a
    log: first
  - id: lp
    loop:
      times: 2
      do:
        - id: b
          sleep: 1s
        - id: c
          if:
            when: "true"
            then:
              - id: d
                log: inside
  - id: e
    key: ctrl+s
"""


def steps_of(text: str) -> list[StepItem]:
    result = load_script(text, lookup=ENGINE_REGISTRY.spec, check=ENGINE_REGISTRY.check_params)
    assert result.script is not None, result.errors
    return parse_steps([step_node(step).to_json() for step in result.script.steps])


def apply(text: str, *edit_list: dict[str, Any] | None) -> str:
    assert all(edit is not None for edit in edit_list)
    payload = EditScriptPayload.from_json({"script_id": "s", "base_version": "v",
                                           "edits": list(edit_list)})
    return apply_edits(text, payload.edits, lookup=ENGINE_REGISTRY.spec)


def shape(text: str) -> list[Any]:
    """The step tree as ids, a block step as ``(id, {block: [...]})``."""

    def node(step: StepItem) -> Any:
        if not step.blocks:
            return step.id
        return (step.id, {k: [node(c) for c in v] for k, v in step.blocks.items()})

    return [node(step) for step in steps_of(text)]


STEPS = steps_of(SCRIPT)


# --- registry and palette ---------------------------------------------------------------


def test_the_registry_reports_blocks_shorthands_and_choices() -> None:
    assert len(REGISTRY) == len(ENGINE_REGISTRY.names())
    if_type = REGISTRY.get("if")
    assert if_type is not None and if_type.blocks == ("then", "else")
    assert if_type.kind == "control" and if_type.required_blocks == ("then",)
    assert REGISTRY.blocks_of("loop") == ("do",)
    assert REGISTRY.blocks_of("click") == ()
    sleep = REGISTRY.get("sleep")
    assert sleep is not None and sleep.shorthand == "duration"
    click = REGISTRY.get("click")
    assert click is not None
    button = click.param("button")
    assert button is not None and button.choices == ("left", "right", "middle")
    assert "Click" in click.tooltip() and "threshold (default 0.9)" in click.tooltip()
    malformed = StepRegistry.from_result({"step_types": [{"doc": "no name"}, 3,
                                                         {"name": "x", "params": [{}]}]})
    assert [t.name for t in malformed] == ["x"]


def test_palette_groups_put_plugin_steps_last_and_filter() -> None:
    plugin = StepType(name="notify", doc="Show a desktop notification.")
    registry = StepRegistry.of([*REGISTRY, plugin])
    groups = palette_groups(registry)
    assert [name for name, _ in groups] == ["Mouse", "Keyboard", "Wait", "Flow", "Data",
                                            "Programs", PLUGINS_GROUP]
    assert [t.name for t in groups[-1][1]] == ["notify"]
    every = [t.name for _, types in groups for t in types]
    assert sorted(every) == sorted(t.name for t in registry)
    assert [(g, [t.name for t in ts]) for g, ts in palette_groups(registry, "WINDOW")] == [
        ("Wait", ["wait_window_in", "wait_window_out"])]
    assert palette_groups(registry, "notification")[0][0] == PLUGINS_GROUP


@pytest.mark.parametrize("name", ENGINE_REGISTRY.names())
def test_a_new_step_of_every_type_keeps_the_script_valid(name: str) -> None:
    step_type = REGISTRY.get(name)
    assert step_type is not None
    text = apply(SCRIPT, edits.insert_edit(new_step(step_type), {"after_step_id": "a"}))
    result = load_script(text, lookup=ENGINE_REGISTRY.spec,
                         check=ENGINE_REGISTRY.check_params)
    assert result.ok, (name, [e.message for e in result.errors])


def test_a_new_step_of_a_plugin_type_fills_its_required_params() -> None:
    plugin = StepType(name="notify", params=(
        ParamInfo("title", "string", required=True),
        ParamInfo("urgency", "string", required=True, choices=("low", "high")),
        ParamInfo("icon", "string"),
        ParamInfo("image", "string", required=True),
        ParamInfo("after", "duration", required=True)))
    assert new_step(plugin) == {"notify": {"title": "", "urgency": "low",
                                           "image": "image.png", "after": "1s"}}
    assert new_step(plugin, ["logo"])["notify"]["image"] == "#logo"


# --- fields -----------------------------------------------------------------------------


def kind(step: str, param: str) -> str:
    step_type = REGISTRY.get(step)
    assert step_type is not None
    spec = step_type.param(param)
    assert spec is not None
    return field_kind(step, spec)


@pytest.mark.parametrize(("step", "param", "expected"), [
    ("click", "image", "image"), ("click", "button", "choice"), ("click", "x", "integer"),
    ("click", "offset", "offset"), ("click", "region", "region"),
    ("click", "window", "window"), ("click", "threshold", "number"),
    ("click", "at", "any"), ("drag", "from", "target"), ("type", "text", "text"),
    ("type", "keyboard", "keyboard"), ("sleep", "duration", "duration"),
    ("if", "when", "condition"), ("loop", "while", "condition"),
    ("loop", "for_each", "any"), ("set", "expr", "expression"), ("exec", "command", "command"),
    ("exec", "env", "mapping"), ("exec", "wait", "boolean"), ("log", "level", "choice"),
    ("wait_image_in", "image", "image"), ("wait_window_in", "title", "string"),
])
def test_field_kinds(step: str, param: str, expected: str) -> None:
    assert kind(step, param) == expected


def test_click_and_move_have_a_target() -> None:
    for name, expected in (("click", True), ("move", True), ("drag", False),
                           ("wait_image_in", False)):
        step_type = REGISTRY.get(name)
        assert step_type is not None
        assert has_target([p.name for p in step_type.params]) is expected


def test_numbers_and_durations() -> None:
    assert parse_integer(" 42 ").value == 42
    assert parse_integer("").value is None
    assert parse_integer("${x}").value == "${x}"
    assert parse_integer("4.5").error == "expected a whole number"
    assert parse_number("0.85").value == 0.85
    assert parse_number("2").value == 2 and isinstance(parse_number("2").value, int)
    assert parse_number("nan").error == "expected a number"
    assert parse_duration_text("2s").value == "2s"
    assert parse_duration_text("2s±20%").value == "2s±20%"
    assert parse_duration_text("1.5").value == 1.5
    assert parse_duration_text("${wait}").value == "${wait}"
    assert parse_duration_text("soon").error is not None
    assert parse_duration_text(" ").value is None


def test_offsets_and_regions() -> None:
    assert parse_offset("0", "1.5h").value == [0, "1.5h"]
    assert parse_offset("-2w", "3").value == ["-2w", 3]
    assert parse_offset("", "").value is None
    assert parse_offset("", "4").value == [0, 4]
    assert "expected pixels or a multiple" in str(parse_offset("1.5x", "0").error)
    assert parse_rect(["10", "20", "300", "200"]).value == [10, 20, 300, 200]
    assert parse_rect(["10", "20", "0", "200"]).error == "the width and height must be positive"
    assert parse_rect(["10", "", "3", "2"]).error == "y: expected a whole number"
    assert region_mode([1, 2, 3, 4]) == "rect"
    assert region_mode({"at": "${a}"}) == "at"
    assert region_mode(None) == "none"


@pytest.mark.parametrize("value", [
    "plain text", 3, 2.5, True, False, None, [1, "a"], {"a": 1}, "42", "true", "[x",
    '"quoted"', " padded ",
])
def test_literals_round_trip(value: Any) -> None:
    parsed = parse_literal(format_literal(value))
    assert parsed.ok
    assert parsed.value == value or (value is None and parsed.value is None)


def test_literal_errors_and_scalars() -> None:
    assert parse_literal("[1, 2").error is not None
    assert parse_literal("hello world").value == "hello world"
    assert parse_scalar_text("1") == 1 and parse_scalar_text("true") is True
    assert parse_scalar_text("C") == "C"


def test_problems_are_mapped_to_fields() -> None:
    problems = [
        {"message": "x: expected an integer", "step_id": "a", "details": {"param": "x"}},
        {"message": "bad from", "step_id": "a", "details": {"param": "from.image"}},
        {"message": "bad timeout", "step_id": "a", "details": {"field": "timeout"}},
        {"message": "no field", "step_id": "a"},
        {"message": "other step", "step_id": "b", "details": {"param": "x"}},
        {"message": "no step"},
    ]
    assert problem_field(problems[1]) == "from"
    by_field, general = step_problems(problems, "a")
    assert by_field == {"x": ["x: expected an integer"], "from": ["bad from"],
                        "timeout": ["bad timeout"]}
    assert general == ["no field"]
    assert problems_by_step(problems) == {
        "a": ["x: expected an integer", "bad from", "bad timeout", "no field"],
        "b": ["other step"]}


def test_summaries_leave_defaults_out() -> None:
    step = StepItem("c", "click", (0,), params={"x": 1, "y": 2, "button": "left",
                                                "threshold": 0.9})
    assert step_summary(step) == "x=1 y=2 button=left threshold=0.9"
    assert step_summary(step, defaults={"button": "left", "threshold": 0.9}) == "x=1 y=2"


# --- header -----------------------------------------------------------------------------


def test_params_value() -> None:
    rows = [ParamRow("customer", description=" Who ", required=False, default="ACME"),
            ParamRow("count", "integer", required=False, default="3"),
            ParamRow("password", secret=True, required=False, default="ignored"),
            ParamRow("who")]
    assert params_value(rows).value == {
        "customer": {"description": "Who", "default": "ACME"},
        "count": {"type": "integer", "default": 3},
        "password": {"secret": True},
        "who": None,
    }
    assert params_value([]).value == {}
    assert "declared twice" in str(params_value([ParamRow("a"), ParamRow("a")]).error)
    assert "a param name" in str(params_value([ParamRow("1a")]).error)
    assert params_value([ParamRow("n", "integer", required=False, default="x")]).error == \
        "n: default: expected a whole number"
    row = ParamRow.from_info({"name": "n", "type": "boolean", "secret": False,
                              "required": False, "default": True})
    assert (row.type, row.required, row.default) == ("boolean", False, "true")


def test_defaults_by_type() -> None:
    assert parse_default("string", " x ").value == " x "
    assert parse_default("boolean", "false").value is False
    assert parse_default("number", "1.5").value == 1.5
    assert parse_default("integer", "1.5").error == "expected a whole number"


def test_vars_value() -> None:
    rows = var_rows({"name": "report", "n": 3, "items": [1, 2], "ok": True, "none": None})
    assert rows == [("name", "report"), ("n", "3"), ("items", "[1, 2]"), ("ok", "true"),
                    ("none", "")]
    assert vars_value(rows).value == {"name": "report", "n": 3, "items": [1, 2],
                                      "ok": True, "none": None}
    assert "not valid JSON" in str(vars_value([("a", "[1,")]).error)


def test_profile_settings_and_images() -> None:
    strategy = MOUSE_SETTINGS[0]
    assert parse_setting(strategy, "linear").value == "linear"
    assert parse_setting(strategy, "fast").error is not None
    assert parse_setting(strategy, "").value is None
    tolerance = next(s for s in MOUSE_SETTINGS if s.name == "tolerance")
    assert parse_setting(tolerance, "2.5").error == "expected a whole number"
    assert image_summary({"name": "a", "width": 48, "height": 20, "bytes": 2048,
                          "scale": 1.5}) == "48 x 20 px, 2.0 KB, scale 1.5"


# --- edits ------------------------------------------------------------------------------


def test_locate_and_contains() -> None:
    location = edits.locate(STEPS, "d")
    assert location is not None and location.parent is not None
    assert (location.parent.id, location.block, location.index) == ("c", "then", 0)
    loop = edits.locate(STEPS, "lp")
    assert loop is not None and edits.contains(loop.step, "d")
    assert edits.locate(STEPS, "nope") is None


def test_insert_positions() -> None:
    assert edits.insert_target(None) == Target()
    assert edits.insert_target("b") == Target("b", "after")
    assert edits.insert_target("b", ("c", "else")) == Target("c", "into", "else")
    new = {"log": {"message": "new"}}
    after_b = edits.position_for(STEPS, REGISTRY, Target("b", "after"))
    assert shape(apply(SCRIPT, edits.insert_edit(new, after_b or {})))[1] == (
        "lp", {"do": ["b", "log_3", ("c", {"then": ["d"]})]})
    into_else = edits.position_for(STEPS, REGISTRY, Target("c", "into", "else"))
    assert into_else == {"parent_step_id": "c", "block": "else"}
    text = apply(SCRIPT, edits.insert_edit(new, into_else))
    assert shape(text)[1][1]["do"][1] == ("c", {"then": ["d"], "else": ["log_3"]})
    before_a = edits.position_for(STEPS, REGISTRY, Target("a", "before"))
    assert before_a == {"index": 0}
    onto_loop = edits.position_for(STEPS, REGISTRY, Target("lp", "into"))
    assert onto_loop == {"parent_step_id": "lp", "block": "do"}
    onto_log = edits.position_for(STEPS, REGISTRY, Target("a", "into"))
    assert onto_log == {"after_step_id": "a"}
    assert edits.position_for(STEPS, REGISTRY, Target()) == {}


def test_move_up_and_down_leave_blocks_at_their_edges() -> None:
    up = edits.move_up(STEPS, "c")
    assert shape(apply(SCRIPT, up))[1] == ("lp", {"do": [("c", {"then": ["d"]}), "b"]})
    out = edits.move_up(STEPS, "b")
    assert shape(apply(SCRIPT, out))[:3] == ["a", "b", ("lp", {"do": [
        ("c", {"then": ["d"]})]})]
    assert edits.move_up(STEPS, "a") is None
    down = edits.move_down(STEPS, "a")
    assert shape(apply(SCRIPT, down))[0][0] == "lp"
    leave = edits.move_down(STEPS, "d")
    assert shape(apply(SCRIPT, leave))[1] == ("lp", {"do": ["b", ("c", {"then": []}), "d"]})
    assert edits.move_down(STEPS, "e") is None
    first_in_block = edits.move_up(steps_of(SCRIPT.replace("  - id: a\n    log: first\n",
                                                           "")), "b")
    assert first_in_block == {"op": "move", "step_id": "b", "to": {"index": 0}}


def test_drops() -> None:
    def drop(step_id: str, target: Target) -> list[Any] | None:
        edit = edits.drop_edit(STEPS, REGISTRY, step_id, target)
        return None if edit is None else shape(apply(SCRIPT, edit))

    assert drop("e", Target("a", "before")) == ["e", "a", ("lp", {"do": [
        "b", ("c", {"then": ["d"]})]})]
    assert drop("a", Target("d", "after")) == [("lp", {"do": [
        "b", ("c", {"then": ["d", "a"]})]}), "e"]
    assert drop("e", Target("c", "into", "else")) == ["a", ("lp", {"do": [
        "b", ("c", {"then": ["d"], "else": ["e"]})]})]
    assert drop("d", Target("lp", "into")) == ["a", ("lp", {"do": [
        "b", ("c", {"then": []}), "d"]}), "e"]
    assert drop("b", Target(None)) == ["a", ("lp", {"do": [("c", {"then": ["d"]})]}),
                                       "e", "b"]
    # Nothing changes, or it cannot apply.
    assert drop("lp", Target("d", "after")) is None
    assert drop("lp", Target("lp", "into")) is None
    assert drop("lp", Target("c", "into", "then")) is None
    assert drop("a", Target("a", "before")) is None
    assert drop("a", Target("lp", "before")) is None
    assert drop("lp", Target("a", "after")) is None
    assert drop("e", Target(None)) is None
    assert drop("d", Target("c", "into", "then")) is None


def test_duplicate_copies_blocks_without_ids_or_defaults() -> None:
    text = SCRIPT.replace("    log: first\n", "    log: first\n    on_error: continue\n"
                                               "    as: {name: out, secret: true}\n")
    steps = steps_of(text)
    copy = edits.step_as_yaml(edits.locate(steps, "a").step, REGISTRY)  # type: ignore[union-attr]
    assert copy == {"log": {"message": "first"}, "on_error": "continue",
                    "as": {"name": "out", "secret": True}}
    plain = steps_of(SCRIPT.replace("    log: first\n", "    log: first\n    as: out\n"))
    assert edits.step_as_yaml(plain[0], REGISTRY)["as"] == "out"
    edit = edits.duplicate_edit(steps, REGISTRY, "lp")
    duplicated = apply(text, edit)
    assert shape(duplicated)[2] == ("loop_2", {"do": ["sleep_2", ("if_2", {"then": [
        "log_3"]})]})
    assert edits.duplicate_edit(steps, REGISTRY, "nope") is None


def test_selection_helpers() -> None:
    assert edits.neighbour_after_delete(STEPS, "a") == "lp"
    assert edits.neighbour_after_delete(STEPS, "e") == "lp"
    assert edits.neighbour_after_delete(STEPS, "d") == "c"
    assert edits.step_at_line(STEPS, 1) is None
    assert edits.step_at_line(STEPS, 5) == "a"
    assert edits.step_at_line(STEPS, 9) == "lp"
    assert edits.step_at_line(STEPS, 10) == "b"
    assert edits.step_at_line(STEPS, 17) == "d"
    assert edits.step_at_line(STEPS, 19) == "e"
