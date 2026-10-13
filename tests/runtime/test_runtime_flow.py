"""Interpreter flow without control commands: steps, blocks, variables, dry-run, seeds."""

from __future__ import annotations

from runtime_helpers import load, run

from j5_macro.runtime.machine import DRY_RUN_WHILE_CAP
from j5_macro.runtime.seeding import derive_seed


def test_linear_script_events_in_order():
    outcome, rec = run("""
        vars: {name: world}
        steps:
          - id: greet
            log: "hello ${name}"
          - id: store
            set: {name: n, value: 5}
    """)
    assert outcome.status == "completed"
    assert outcome.error is None
    assert rec.summary() == [
        "state_changed:running/None",
        "step_started:greet",
        "log:greet",
        "step_finished:greet",
        "step_started:store",
        "variable_changed:store",
        "step_finished:store",
    ]
    started = rec.of("step_started", step_id="greet")[0]
    assert started == {"step_id": "greet", "path": [0], "line": 4, "type": "log",
                       "params": {"message": "hello world", "level": "info"}}
    assert rec.of("log")[0] == {"level": "info", "message": "hello world", "step_id": "greet"}
    assert rec.of("variable_changed")[0] == {"name": "n", "old": None, "new": 5,
                                             "step_id": "store"}
    finished = rec.of("step_finished", step_id="store")[0]
    assert finished["result"] == {"name": "n", "value": 5}
    assert isinstance(finished["duration_ms"], int)


NESTED = """
    vars: {k: 0}
    steps:
      - id: outer
        loop:
          times: 2
          do:
            - id: inner
              loop:
                for_each: [a, b]
                as: letter
                do:
                  - id: branch
                    if:
                      when: "letter == 'a'"
                      then:
                        - id: say_a
                          log: "A${k}"
                      else:
                        - id: say_b
                          log: "B"
            - id: bump
              set: {name: k, expr: "k + 1"}
"""


def test_nested_blocks_paths_and_iterations():
    outcome, rec = run(NESTED)
    assert outcome.status == "completed"
    assert [p["message"] for p in rec.of("log")] == ["A0", "B", "A1", "B"]

    say_a = rec.of("step_started", step_id="say_a")
    assert [p["path"] for p in say_a] == [[0, 0, 0, 0, 0, 0, 0]] * 2
    assert [p["iterations"] for p in say_a] == [[0, 0], [1, 0]]
    say_b = rec.of("step_started", step_id="say_b")
    assert [p["path"] for p in say_b] == [[0, 0, 0, 0, 0, 1, 0]] * 2
    assert [p["iterations"] for p in say_b] == [[0, 1], [1, 1]]
    assert [p["iterations"] for p in rec.of("step_started", step_id="bump")] == [[0], [1]]

    outer = rec.of("loop_iteration", step_id="outer")
    assert [(p["index"], p["total"], p.get("iterations")) for p in outer] == [
        (0, 2, None), (1, 2, None)]
    inner = rec.of("loop_iteration", step_id="inner")
    assert [(p["index"], p["iterations"]) for p in inner] == [(0, [0]), (1, [0]), (0, [1]),
                                                               (1, [1])]
    branches = rec.of("branch_taken")
    assert [(b["branch"], b["value"], b["iterations"]) for b in branches] == [
        ("then", True, [0, 0]), ("else", False, [0, 1]),
        ("then", True, [1, 0]), ("else", False, [1, 1])]
    assert branches[0]["condition"] == "letter == 'a'"

    finished = {p["step_id"]: p for p in rec.of("step_finished")}
    assert finished["outer"]["result"] == {"iterations": 2}
    assert "iterations" not in finished["outer"]
    assert finished["branch"]["result"] == {"branch": "else"}
    assert [p["new"] for p in rec.of("variable_changed", name="letter")] == ["a", "b"] * 2
    # every started step is closed
    assert len(rec.of("step_started")) == len(rec.of("step_finished"))


def test_block_step_closes_after_its_children():
    _, rec = run("""
        steps:
          - id: check
            if:
              when: "true"
              then:
                - id: inside
                  log: x
          - id: after
            log: y
    """)
    assert rec.summary()[1:] == [
        "step_started:check", "branch_taken:check",
        "step_started:inside", "log:inside", "step_finished:inside",
        "step_finished:check",
        "step_started:after", "log:after", "step_finished:after",
    ]


def test_if_without_else_and_empty_loop():
    outcome, rec = run("""
        steps:
          - id: no
            if:
              when: "1 > 2"
              then:
                - log: never
          - id: zero
            loop:
              times: 0
              do:
                - log: never
    """)
    assert outcome.status == "completed"
    assert rec.of("log") == []
    finished = {p["step_id"]: p["result"] for p in rec.of("step_finished")}
    assert finished == {"no": {"branch": "else"}, "zero": {"iterations": 0}}
    assert rec.of("loop_iteration") == []


def test_for_each_with_expression_and_interpolation():
    outcome, rec = run("""
        vars: {items: [1, 2, 3]}
        steps:
          - id: by_expr
            loop:
              for_each: "items"
              as: n
              do:
                - log: "n=${n}"
          - id: by_template
            loop:
              for_each: "${items}"
              do:
                - log: "item=${item}"
    """)
    assert outcome.status == "completed"
    messages = [p["message"] for p in rec.of("log")]
    assert messages == ["n=1", "n=2", "n=3", "item=1", "item=2", "item=3"]
    assert rec.of("loop_iteration", step_id="by_expr")[0]["total"] == 3
    started = rec.of("step_started", step_id="by_template")[0]
    assert started["params"]["for_each"] == [1, 2, 3]


def test_for_each_requires_a_list():
    outcome, _ = run("""
        vars: {n: 3}
        steps:
          - id: bad
            loop:
              for_each: "n + 1"
              do:
                - log: x
    """)
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "validation_failed"
    assert outcome.error.step_id == "bad"


def test_while_with_counter():
    outcome, rec = run("""
        vars: {i: 0}
        steps:
          - id: count
            loop:
              while: "i < 3"
              do:
                - set: {name: i, expr: "i + 1"}
    """)
    assert outcome.status == "completed"
    iterations = rec.of("loop_iteration", step_id="count")
    assert [p["index"] for p in iterations] == [0, 1, 2]
    assert all("total" not in p for p in iterations)
    assert [p["new"] for p in rec.of("variable_changed", name="i")] == [1, 2, 3]
    assert rec.of("step_finished", step_id="count")[0]["result"] == {"iterations": 3}


def test_interpolation_preserves_types():
    outcome, rec = run("""
        vars: {items: [1, 2], n: 4, px: 5}
        steps:
          - id: copy
            set: {name: copy, value: "${items}"}
          - id: text
            set: {name: text, value: "n=${n}"}
          - id: number
            set: {name: num, value: "${n * 2}"}
          - id: click
            click: {x: "${px}", y: 7}
    """, mode="dry")
    assert outcome.status == "completed"
    news = {p["name"]: p["new"] for p in rec.of("variable_changed")}
    assert news == {"copy": [1, 2], "text": "n=4", "num": 8}
    assert rec.of("step_started", step_id="click")[0]["params"]["x"] == 5


def test_type_is_checked_after_interpolation():
    outcome, rec = run("""
        vars: {px: left}
        steps:
          - id: click
            click: {x: "${px}", y: 7}
    """, mode="dry")
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "validation_failed"
    assert outcome.error.details == {"param": "x", "attempt": 1}
    started = rec.of("step_started", step_id="click")[0]
    assert started["params"]["x"] == "${px}"  # raw, since it could not be resolved
    assert rec.summary()[-1] == "step_failed:click"


def test_interpolation_error_is_expression_error():
    outcome, _ = run("""
        steps:
          - id: say
            log: "${missing + 1}"
    """)
    assert outcome.error is not None
    assert outcome.error.code == "expression_error"


def test_set_value_and_expr_and_variable_overrides():
    outcome, rec = run("""
        vars: {a: 1, b: 2}
        steps:
          - id: s1
            set: {name: a, expr: "a + b"}
          - id: s2
            set: {name: c, value: "${a}"}
    """, variables={"b": 10})
    assert outcome.status == "completed"
    changes = [(p["name"], p.get("old"), p["new"], p["step_id"])
               for p in rec.of("variable_changed")]
    assert changes == [("a", 1, 11, "s1"), ("c", None, 11, "s2")]


def test_executor_exception_becomes_internal_error():
    outcome, rec = run("""
        steps:
          - id: bad
            boom: {}
    """)
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "internal_error"
    assert outcome.error.step_id == "bad"
    assert outcome.error.line == 3
    assert outcome.error.details is not None
    assert "kaput" in outcome.error.details["traceback"]
    assert "RuntimeError" in outcome.error.message
    assert rec.of("step_failed")[0]["error"]["code"] == "internal_error"


def test_unknown_predefined_condition_fails_in_normal_mode():
    outcome, _ = run("""
        steps:
          - id: look
            if:
              when: {text_visible: hello}
              then:
                - log: seen
    """)
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "expression_error"
    assert "unknown predefined condition 'text_visible'" in outcome.error.message


def test_injected_condition_receives_interpolated_argument():
    seen = []

    def visible(ctx, argument):
        seen.append(argument)
        return True

    outcome, rec = run("""
        vars: {name: menu}
        steps:
          - id: look
            if:
              when: {image_visible: "img/${name}.png"}
              then:
                - log: seen
    """, conditions={"image_visible": visible})
    assert outcome.status == "completed"
    assert seen == ["img/menu.png"]
    assert rec.of("branch_taken")[0]["condition"] == {"image_visible": "img/${name}.png"}


# --- dry-run --------------------------------------------------------------------------


def test_input_steps_fail_outside_dry_run():
    outcome, _ = run("""
        steps:
          - id: c
            click: {x: 1, y: 2}
    """)
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"


def test_dry_run_describes_input_and_runs_the_rest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "x.png").write_bytes(b"")
    outcome, rec = run("""
        vars: {n: 0}
        steps:
          - id: c
            click: {x: 1, y: 2}
          - id: look
            if:
              when: {image_visible: img/x.png}
              then:
                - log: seen
              else:
                - id: unseen
                  set: {name: n, value: 1}
          - id: nap
            sleep: 1s
    """, mode="dry")
    assert outcome.status == "completed"
    assert rec.summary()[0] == "state_changed:running/None"
    assert rec.of("step_finished", step_id="c")[0]["result"] == {
        "dry_run": True, "description": "would click left at (1, 2)"}
    warnings = rec.of("log", level="warning", step_id="look")
    assert "treated as false in dry-run" in warnings[0]["message"]
    assert rec.of("branch_taken")[0]["branch"] == "else"
    assert rec.of("variable_changed", name="n")[0]["new"] == 1
    assert rec.of("step_finished", step_id="nap")[0]["result"] == {"seconds": 1.0}


def test_dry_run_caps_while_loops():
    outcome, rec = run("""
        steps:
          - id: forever
            loop:
              while: "true"
              do:
                - set: {name: x, value: 1}
    """, mode="dry")
    assert outcome.status == "completed"
    assert len(rec.of("loop_iteration")) == DRY_RUN_WHILE_CAP
    warning = rec.of("log", level="warning", step_id="forever")
    assert len(warning) == 1
    assert str(DRY_RUN_WHILE_CAP) in warning[0]["message"]
    assert rec.of("step_finished", step_id="forever")[0]["result"] == {
        "iterations": DRY_RUN_WHILE_CAP}


# --- seeds ----------------------------------------------------------------------------

RANDOM = """
    steps:
      - id: twice
        loop:
          times: 2
          do:
            - id: r
              rand: {}
"""


def _values(**options):
    outcome, rec = run(load(RANDOM), **options)
    assert outcome.status == "completed"
    return [p["result"]["value"] for p in rec.of("step_finished", step_id="r")]


def test_rng_is_deterministic_per_seed_and_differs_per_iteration():
    first = _values(seed=7)
    assert first == _values(seed=7)
    assert first[0] != first[1]
    assert first != _values(seed=8)


def test_derive_seed_is_stable_and_uses_every_input():
    base = derive_seed(1, "s", [0, 1], 1)
    assert base == derive_seed(1, "s", [0, 1], 1)
    assert len({base, derive_seed(2, "s", [0, 1], 1), derive_seed(1, "t", [0, 1], 1),
                derive_seed(1, "s", [1, 1], 1), derive_seed(1, "s", [0, 1], 2),
                derive_seed(1, "s", None, 1)}) == 6
    assert 0 <= base < 2**64
