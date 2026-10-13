"""Tests for the structured script editor (docs/contract.md, "Script management")."""

from __future__ import annotations

import difflib
from typing import Any

import pytest

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads import EditScriptPayload
from j5_macro.protocol.payloads.codec import PayloadError
from j5_macro.script.editor import EditError, Indent, apply_edits, detect_indent
from j5_macro.script.loader import load_script
from j5_macro.script.model import Script, Step
from j5_macro.steps import default_registry

REGISTRY = default_registry()

BASIC = """\
version: 1
name: Basic
steps:
  - id: greet
    log: hello
  - id: lp
    loop:
      times: 2
      do:
        - id: inner
          sleep: 1s
  - id: cond
    if:
      when: "x == 1"
      then:
        - id: yes
          log: yes
  - id: last
    key: ctrl+s
"""

COMMENTED = """\
# A script full of comments and styles.
version: 1
name: 'Commented'   # single-quoted on purpose
input:
  mouse:    {strategy: human, speed: 1.0}   # aligned
  keyboard: {strategy: human, wpm: 55}
params:
  customer: {description: "Name to type", default: "ACME"}
vars:
  filename: "october_report"  # double quotes
  count: 0x10
steps:
  # Open the menu first.
  - id: open_menu
    click: {image: img/file_menu.png, threshold: 0.90}  # 0.90 stays 0.90
  - id: wait_dialog
    wait_image: {image: 'img/save dialog.png', timeout: 10s}
    on_error: stop

  # Typing.
  - id: typing
    type: {text: "${filename}", keyboard: {strategy: direct}}
  - id: confirm
    break: true
    key: {press: enter}   # confirm
  - id: retry
    loop:
      times: 3
      do:
        - id: check
          if:
            when: {image_visible: img/error.png}
            then:
              - id: retry_click
                click: {image: img/retry.png}
              - id: nap
                sleep: 2s±20%  # jittered
# Trailing comment.
"""


def edit(text: str, *edits: dict[str, Any]) -> str:
    payload = EditScriptPayload.from_json({"script_id": "s1", "base_version": "v",
                                           "edits": list(edits)})
    return apply_edits(text, payload.edits, lookup=REGISTRY.spec)


def load(text: str) -> Script:
    result = load_script(text, lookup=REGISTRY.spec, check=REGISTRY.check_params)
    assert result.script is not None, result.errors
    return result.script


def tree(text: str) -> list[Any]:
    """The step tree as ``id`` or ``(id, {block: [...]})``."""

    def node(step: Step) -> Any:
        if not step.blocks:
            return step.id
        return (step.id, {slot: [node(c) for c in children]
                          for slot, children in step.blocks.items()})

    return [node(step) for step in load(text).steps]


def changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    """Lines removed from ``before`` and added in ``after``."""
    removed: list[str] = []
    added: list[str] = []
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(),
                                      autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op != "equal":
            removed += before.splitlines()[a1:a2]
            added += after.splitlines()[b1:b2]
    return removed, added


def refused(text: str, *edits: dict[str, Any]) -> EditError:
    with pytest.raises(EditError) as info:
        edit(text, *edits)
    return info.value


# --- preservation and ids ---------------------------------------------------------------


def test_no_edits_keeps_a_file_with_ids_byte_identical() -> None:
    assert edit(COMMENTED) == COMMENTED
    assert edit(BASIC) == BASIC


def test_an_update_changes_only_its_line() -> None:
    out = edit(COMMENTED, {"op": "update", "step_id": "nap", "params": {"duration": "3s"}})
    assert changed_lines(COMMENTED, out) == (
        ["                sleep: 2s±20%  # jittered"],
        ["                sleep: 3s      # jittered"],  # comments keep their column
    )


def test_untouched_lines_are_byte_identical_after_structural_edits() -> None:
    out = edit(
        COMMENTED,
        {"op": "insert", "step": {"log": "inserted"}, "to": {"after_step_id": "typing"}},
        {"op": "delete", "step_id": "wait_dialog"},
        {"op": "update", "step_id": "open_menu", "params": {"threshold": 0.8}},
    )
    removed, added = changed_lines(COMMENTED, out)
    assert removed == [
        "    click: {image: img/file_menu.png, threshold: 0.90}  # 0.90 stays 0.90",
        "  - id: wait_dialog",
        "    wait_image: {image: 'img/save dialog.png', timeout: 10s}",
        "    on_error: stop",
    ]
    assert added == [
        "    click: {image: img/file_menu.png, threshold: 0.8}   # 0.90 stays 0.90",
        "  - id: log_1",
        "    log: inserted",
    ]
    assert tree(out)[:4] == ["open_menu", "typing", "log_1", "confirm"]


GENERATED = """\
steps:
  - log: first            # log_1_2: log_1 is taken below
  - id: log_1
    log: second
  - click: {x: 1, y: 2}   # click_1
  - loop:                 # loop_1
      times: 2
      do:
        - log: inner      # log_3
        - if:             # if_1
            when: "true"
            else:
              - sleep: 1s # sleep_1
            then:
              - click: {x: 3, y: 4}  # click_2
  - id: click_3
    click: {x: 5, y: 6}
  - click: {x: 7, y: 8}   # click_4
"""


def test_written_ids_are_the_ones_the_loader_generates() -> None:
    before = [step.id for step in load(GENERATED).walk()]
    assert before == ["log_1_2", "log_1", "click_1", "loop_1", "log_3", "if_1", "sleep_1",
                      "click_2", "click_3", "click_4"]
    out = edit(GENERATED)
    script = load(out)
    assert [step.id for step in script.walk()] == before
    assert not [w for w in script.warnings if (w.details or {}).get("generated_id")]
    # Each step without an id gets one written before its type key; nothing else changes.
    assert out.replace("- id: ", "").count("\n") == GENERATED.count("\n") + 8
    assert "  - id: log_1_2\n    log: first            # log_1_2: log_1 is taken below\n" in out
    assert "              - id: sleep_1\n                sleep: 1s # sleep_1\n" in out


def test_edits_address_steps_by_their_generated_ids() -> None:
    out = edit(GENERATED, {"op": "delete", "step_id": "log_1_2"},
               {"op": "update", "step_id": "sleep_1", "params": {"duration": "5s"}})
    script = load(out)
    assert script.steps[0].id == "log_1"
    assert script.find("sleep_1") is not None
    assert script.find("sleep_1").params["duration"] == "5s"  # type: ignore[union-attr]
    assert script.find("click_2") is not None


def test_an_invalid_script_can_be_edited_and_keeps_a_bad_id() -> None:
    text = "steps:\n  - id: 3\n    log: a\n  - log: {}\n"
    out = edit(text, {"op": "update", "step_id": "log_2", "params": {"message": "fixed"}})
    assert out == "steps:\n  - id: 3\n    log: a\n  - id: log_2\n    log: {message: fixed}\n"


# --- insert -----------------------------------------------------------------------------


def test_insert_at_top_level_positions() -> None:
    out = edit(
        BASIC,
        {"op": "insert", "step": {"log": "end"}, "to": {}},
        {"op": "insert", "step": {"log": "start", "id": "first"}, "to": {"index": 0}},
        {"op": "insert", "step": {"sleep": "1s"}, "to": {"after_step_id": "greet"}},
    )
    assert tree(out) == ["first", "greet", "sleep_2", ("lp", {"do": ["inner"]}),
                         ("cond", {"then": ["yes"]}), "last", "log_3"]
    assert "  - id: first\n    log: start\n  - id: greet\n" in out
    assert out.endswith("  - id: last\n    key: ctrl+s\n  - id: log_3\n    log: end\n")


def test_insert_into_blocks_and_create_else() -> None:
    out = edit(
        BASIC,
        {"op": "insert", "step": {"log": "in do"},
         "to": {"parent_step_id": "lp", "block": "do", "index": 0}},
        {"op": "insert", "step": {"log": "in then"},
         "to": {"parent_step_id": "cond", "block": "then"}},
        {"op": "insert", "step": {"log": "in else"},
         "to": {"parent_step_id": "cond", "block": "else"}},
        {"op": "insert", "step": {"log": "after inner"}, "to": {"after_step_id": "inner"}},
    )
    assert tree(out) == ["greet", ("lp", {"do": ["log_3", "inner", "log_6"]}),
                         ("cond", {"then": ["yes", "log_4"], "else": ["log_5"]}), "last"]
    assert ("      then:\n        - id: yes\n          log: yes\n"
            "        - id: log_4\n          log: in then\n"
            "      else:\n        - id: log_5\n          log: in else\n") in out


def test_insert_a_step_with_blocks_and_common_fields() -> None:
    step = {"if": {"when": "a > 1", "then": [{"log": "big"}, {"id": "nap", "sleep": "1s"}]},
            "on_error": "continue", "input": {"mouse": {"strategy": "direct"}}}
    out = edit("steps: []\n", {"op": "insert", "step": step, "to": {}})
    assert out == (
        "steps:\n"
        "  - id: if_1\n"
        "    if:\n"
        "      when: a > 1\n"
        "      then:\n"
        "        - id: log_1\n"
        "          log: big\n"
        "        - id: nap\n"
        "          sleep: 1s\n"
        "    on_error: continue\n"
        "    input:\n"
        "      mouse: {strategy: direct}\n"
    )
    assert tree(out) == [("if_1", {"then": ["log_1", "nap"]})]


def test_insert_writes_params_in_flow_style_and_quotes_what_needs_it() -> None:
    out = edit("steps:\n", {"op": "insert", "to": {}, "step": {
        "click": {"x": 10, "y": 20, "window": {"title": "Notes"}}}},
        {"op": "insert", "to": {}, "step": {"type": {"text": "true"}}})
    assert out == ("steps:\n"
                   "  - id: click_1\n"
                   "    click:\n"
                   "      x: 10\n"
                   "      y: 20\n"
                   "      window: {title: Notes}\n"
                   "  - id: type_1\n"
                   "    type: {text: 'true'}\n")
    assert load(out).steps[1].params["text"] == "true"


def test_insert_ids_skip_used_ones() -> None:
    text = "steps:\n  - id: log_2\n    log: a\n"
    out = edit(text, {"op": "insert", "step": {"log": "b"}, "to": {}},
               {"op": "insert", "step": {"log": "c"}, "to": {}})
    assert [s.id for s in load(out).steps] == ["log_2", "log_3", "log_4"]


@pytest.mark.parametrize(("step", "to", "reason"), [
    ({"log": "x", "id": "greet"}, {}, "already used"),
    ({"log": "x", "id": "inner"}, {}, "already used"),
    ({"log": "x", "id": "9lives"}, {}, "invalid id"),
    ({"log": "x", "sleep": "1s"}, {}, "exactly one step type"),
    ({"id": "lonely"}, {}, "exactly one step type"),
    ({"frobnicate": {}}, {}, "unknown step type"),
    ({"log": "x"}, {"after_step_id": "nope"}, "unknown step id"),
    ({"log": "x"}, {"parent_step_id": "nope", "block": "do"}, "unknown step id"),
    ({"log": "x"}, {"parent_step_id": "lp", "block": "then"}, "has no block 'then'"),
    ({"log": "x"}, {"parent_step_id": "cond", "block": "do"}, "has no block 'do'"),
    ({"log": "x"}, {"parent_step_id": "greet", "block": "do"}, "has no block 'do'"),
    ({"log": "x"}, {"index": 9}, "past the end"),
    ({"if": {"when": "x", "then": [{"log": "a", "id": "greet"}]}}, {}, "already used"),
    ({"if": {"when": "x", "then": [{"log": "a", "id": "d"}, {"log": "b", "id": "d"}]}}, {},
     "already used"),
])
def test_insert_errors(step: dict[str, Any], to: dict[str, Any], reason: str) -> None:
    error = refused(BASIC, {"op": "insert", "step": step, "to": to})
    assert error.index == 0
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert reason in error.message


def test_a_missing_then_is_not_created() -> None:
    text = "steps:\n  - id: c\n    if: {when: x}\n"
    error = refused(text, {"op": "insert", "step": {"log": "a"},
                           "to": {"parent_step_id": "c", "block": "then"}})
    assert "no 'then' block" in error.message


# --- update -----------------------------------------------------------------------------


def test_update_params_set_replace_and_remove() -> None:
    text = "steps:\n  - id: c\n    click: {x: 1, y: 2, button: right}\n"
    out = edit(text, {"op": "update", "step_id": "c",
                      "params": {"x": 5, "button": None, "clicks": 2}})
    assert out == "steps:\n  - id: c\n    click: {x: 5, y: 2, clicks: 2}\n"


def test_update_keeps_the_short_form_while_it_fits() -> None:
    out = edit(BASIC, {"op": "update", "step_id": "inner", "params": {"duration": "3s"}})
    assert "          sleep: 3s\n" in out
    out = edit(BASIC, {"op": "update", "step_id": "greet", "params": {"level": "warning"}})
    assert "    log: {message: hello, level: warning}\n" in out
    assert load(out).steps[0].params == {"message": "hello", "level": "warning"}
    # Once a mapping, it stays a mapping.
    again = edit(out, {"op": "update", "step_id": "greet", "params": {"level": None}})
    assert "    log: {message: hello}\n" in again


def test_update_a_step_without_parameters() -> None:
    text = "steps:\n  - id: p\n    pause:\n"
    out = edit(text, {"op": "update", "step_id": "p", "params": {"message": "check"}})
    assert out == "steps:\n  - id: p\n    pause: {message: check}\n"


def test_update_block_step_params_and_common_fields() -> None:
    out = edit(BASIC, {"op": "update", "step_id": "lp", "params": {"times": 5},
                       "common": {"break": True, "on_error": "continue"}},
               {"op": "update", "step_id": "last", "common": {"timeout": "2s"}})
    assert ("  - id: lp\n    loop:\n      times: 5\n      do:\n        - id: inner\n"
            "          sleep: 1s\n    break: true\n    on_error: continue\n") in out
    assert out.endswith("    key: ctrl+s\n    timeout: 2s\n")
    removed = edit(out, {"op": "update", "step_id": "lp", "common": {"break": None}})
    assert "break" not in removed
    assert load(removed).find("lp").on_error == "continue"  # type: ignore[union-attr]


def test_update_the_as_common_field() -> None:
    out = edit(BASIC, {"op": "update", "step_id": "last",
                       "common": {"as": {"name": "token", "secret": True}}})
    assert out.endswith("    key: ctrl+s\n    as: {name: token, secret: true}\n")
    step = load(out).find("last")
    assert step is not None and step.store_as is not None
    assert (step.store_as.name, step.store_as.secret) == ("token", True)
    short = edit(out, {"op": "update", "step_id": "last", "common": {"as": "pressed"}})
    assert short.endswith("    key: ctrl+s\n    as: pressed\n")
    removed = edit(short, {"op": "update", "step_id": "last", "common": {"as": None}})
    assert "as:" not in removed


def test_update_nested_mapping_keeps_unchanged_parts() -> None:
    out = edit(COMMENTED, {"op": "update", "step_id": "typing",
                           "params": {"keyboard": {"strategy": "direct", "wpm": 90}}})
    assert changed_lines(COMMENTED, out) == (
        ['    type: {text: "${filename}", keyboard: {strategy: direct}}'],
        ['    type: {text: "${filename}", keyboard: {strategy: direct, wpm: 90}}'],
    )


@pytest.mark.parametrize(("edit_", "reason"), [
    ({"step_id": "nope", "params": {"x": 1}}, "unknown step id"),
    ({"step_id": "lp", "params": {"do": []}}, "blocks are not parameters"),
    ({"step_id": "greet", "common": {"id": "other"}}, "not a common field"),
    ({"step_id": "greet", "common": {"frob": 1}}, "not a common field"),
])
def test_update_errors(edit_: dict[str, Any], reason: str) -> None:
    error = refused(BASIC, {"op": "update", **edit_})
    assert reason in error.message


# --- move and delete --------------------------------------------------------------------


def test_move_within_and_across_blocks() -> None:
    out = edit(
        BASIC,
        {"op": "move", "step_id": "last", "to": {"index": 0}},
        {"op": "move", "step_id": "greet", "to": {"parent_step_id": "lp", "block": "do"}},
        {"op": "move", "step_id": "yes", "to": {"parent_step_id": "cond", "block": "else"}},
        {"op": "move", "step_id": "inner", "to": {"after_step_id": "cond"}},
    )
    assert tree(out) == ["last", ("lp", {"do": ["greet"]}),
                         ("cond", {"then": [], "else": ["yes"]}), "inner"]


def test_move_index_is_the_position_in_the_resulting_list() -> None:
    out = edit(BASIC, {"op": "move", "step_id": "greet", "to": {"index": 2}})
    assert tree(out) == [("lp", {"do": ["inner"]}), ("cond", {"then": ["yes"]}), "greet",
                         "last"]
    out = edit(BASIC, {"op": "move", "step_id": "greet", "to": {"index": 3}})
    assert [s.id for s in load(out).steps] == ["lp", "cond", "last", "greet"]
    assert refused(BASIC, {"op": "move", "step_id": "greet", "to": {"index": 4}})


def test_move_a_block_with_its_content() -> None:
    out = edit(BASIC, {"op": "move", "step_id": "lp",
                       "to": {"parent_step_id": "cond", "block": "then", "index": 0}})
    assert tree(out) == ["greet", ("cond", {"then": [("lp", {"do": ["inner"]}), "yes"]}),
                         "last"]
    assert ("      then:\n        - id: lp\n          loop:\n            times: 2\n"
            "            do:\n              - id: inner\n                sleep: 1s\n") in out


def test_move_after_itself_changes_nothing() -> None:
    assert edit(BASIC, {"op": "move", "step_id": "lp", "to": {"after_step_id": "lp"}}) == BASIC


@pytest.mark.parametrize("to", [
    {"parent_step_id": "lp", "block": "do"},
    {"after_step_id": "inner"},
])
def test_a_block_cannot_move_into_itself(to: dict[str, Any]) -> None:
    error = refused(BASIC, {"op": "move", "step_id": "lp", "to": to})
    assert "into itself" in error.message


def test_a_block_cannot_move_into_a_descendant() -> None:
    text = edit(BASIC, {"op": "move", "step_id": "cond",
                        "to": {"parent_step_id": "lp", "block": "do"}})
    error = refused(text, {"op": "move", "step_id": "lp",
                           "to": {"parent_step_id": "cond", "block": "else"}})
    assert "into itself" in error.message


def test_delete_steps_and_blocks() -> None:
    out = edit(BASIC, {"op": "delete", "step_id": "inner"}, {"op": "delete", "step_id": "cond"})
    assert tree(out) == ["greet", ("lp", {"do": []}), "last"]
    assert refused(BASIC, {"op": "delete", "step_id": "yes"},
                   {"op": "delete", "step_id": "yes"}).index == 1


# --- comments follow steps --------------------------------------------------------------


def test_comments_above_a_step_move_with_it_and_tails_stay() -> None:
    out = edit(COMMENTED, {"op": "move", "step_id": "typing",
                           "to": {"parent_step_id": "retry", "block": "do", "index": 0}},
               {"op": "delete", "step_id": "nap"})
    assert ("      do:\n"
            "\n"
            "        # Typing.\n"
            "        - id: typing\n") in out
    assert "    on_error: stop\n  - id: confirm\n" in out
    assert out.endswith("                click: {image: img/retry.png}\n# Trailing comment.\n")


def test_a_step_appended_at_the_end_goes_before_the_trailing_comment() -> None:
    out = edit(COMMENTED, {"op": "insert", "step": {"log": "bye"}, "to": {}})
    assert out.endswith("  - id: log_1\n    log: bye\n# Trailing comment.\n")
    out = edit(COMMENTED, {"op": "update", "step_id": "retry", "common": {"break": True}})
    assert out.endswith("# jittered\n    break: true\n# Trailing comment.\n")


# --- set_meta ---------------------------------------------------------------------------


def test_set_meta_changes_and_removes_header_keys() -> None:
    out = edit(COMMENTED, {"op": "set_meta", "name": "Renamed",
                           "vars": {"filename": "october_report", "count": 16, "extra": True},
                           "params": {}})
    removed, added = changed_lines(COMMENTED, out)
    assert removed == ["name: 'Commented'   # single-quoted on purpose",
                       "params:",
                       '  customer: {description: "Name to type", default: "ACME"}']
    assert added == ["name: 'Renamed'     # single-quoted on purpose", "  extra: true"]
    script = load(out)
    assert script.name == "Renamed"
    assert script.params == {}
    assert script.vars == {"filename": "october_report", "count": 16, "extra": True}


def test_set_meta_adds_missing_keys_in_header_order() -> None:
    out = edit(BASIC, {"op": "set_meta", "input": {"mouse": {"strategy": "direct"}},
                       "params": {"who": {"description": "Name"}}, "vars": {"n": 1}})
    assert out.startswith("version: 1\nname: Basic\ninput:\n  mouse: {strategy: direct}\n"
                          "params:\n  who: {description: Name}\nvars:\n  n: 1\nsteps:\n")
    assert edit(out, {"op": "set_meta", "input": {}, "params": {}, "vars": {}}) == BASIC
    named = edit("steps: []\n", {"op": "set_meta", "name": "New"})
    assert named == "name: New\nsteps: []\n"


def test_set_meta_sets_the_capture_scale() -> None:
    out = edit(BASIC, {"op": "set_meta", "capture_scale": 1.5})
    assert out.startswith("version: 1\nname: Basic\ncapture_scale: 1.5\nsteps:\n")
    assert load(out).capture_scale == 1.5
    again = edit(out, {"op": "set_meta", "capture_scale": 1.25})
    assert again == out.replace("capture_scale: 1.5", "capture_scale: 1.25")
    with pytest.raises(PayloadError):
        edit(BASIC, {"op": "set_meta", "capture_scale": 0})


# --- several edits, errors, formats -----------------------------------------------------


def test_edits_apply_in_order_and_the_result_reloads() -> None:
    out = edit(
        BASIC,
        {"op": "insert", "step": {"id": "t", "type": "hi"}, "to": {"after_step_id": "greet"}},
        {"op": "move", "step_id": "t", "to": {"parent_step_id": "cond", "block": "else"}},
        {"op": "update", "step_id": "t", "params": {"text": "bye"}},
        {"op": "delete", "step_id": "greet"},
    )
    assert tree(out) == [("lp", {"do": ["inner"]}),
                         ("cond", {"then": ["yes"], "else": ["t"]}), "last"]
    assert load(out).find("t").params["text"] == "bye"  # type: ignore[union-attr]


def test_a_failing_edit_names_its_index() -> None:
    error = refused(BASIC, {"op": "delete", "step_id": "greet"},
                    {"op": "update", "step_id": "greet", "params": {"level": "debug"}})
    assert error.index == 1
    assert error.message.startswith("edit 1 (update): unknown step id 'greet'")


def test_invalid_yaml_is_a_parse_error() -> None:
    error = refused("steps: [\n", {"op": "delete", "step_id": "x"})
    assert error.code == ErrorCode.PARSE_ERROR
    assert error.index is None
    assert error.line is not None
    error = refused("- a\n- b\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.details == {"reason": "not_a_mapping"}


def test_steps_is_created_when_missing() -> None:
    out = edit("name: x\n", {"op": "insert", "step": {"log": "a"}, "to": {}})
    assert out == "name: x\nsteps:\n  - id: log_1\n    log: a\n"
    assert "not a list" in refused("steps: 3\n", {"op": "insert", "step": {"log": "a"},
                                                  "to": {}}).message


ZERO_OFFSET = """\
---
name: zero
input:
    mouse: {strategy: direct}
steps:
- id: a
  log: a
- id: b
  loop:
      times: 1
      do:
      - id: c
        log: c
"""


def test_indentation_is_detected() -> None:
    assert detect_indent(BASIC) == Indent(mapping=2, sequence=4, offset=2)
    assert detect_indent(ZERO_OFFSET) == Indent(mapping=4, sequence=2, offset=0)
    assert detect_indent("steps: []\n") == Indent()
    assert edit(ZERO_OFFSET) == ZERO_OFFSET
    out = edit(ZERO_OFFSET, {"op": "insert", "step": {"log": "d"},
                             "to": {"parent_step_id": "b", "block": "do"}})
    assert out.endswith("      - id: c\n        log: c\n      - id: log_3\n        log: d\n")


def test_windows_line_ends_become_newlines() -> None:
    out = edit(BASIC.replace("\n", "\r\n"), {"op": "delete", "step_id": "last"})
    assert "\r" not in out
    assert out == BASIC.replace("  - id: last\n    key: ctrl+s\n", "")
