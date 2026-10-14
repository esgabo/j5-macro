"""Embedded images: the ``images`` section, its checks and ``#name`` references in the
loader, the helpers of ``j5_macro.script.images``, the structured editor and the JSON
Schema."""

from __future__ import annotations

from typing import Any

import pytest

from j5_macro.protocol.payloads import (
    AddImageEdit,
    DeleteImageEdit,
    RenameImageEdit,
    SetMetaEdit,
    UpdateEdit,
)
from j5_macro.script import images as embedded
from j5_macro.script.editor import EditError, apply_edits, rewrite_images
from j5_macro.script.loader import LoadResult, load_script
from j5_macro.steps import default_registry
from j5_macro.steps.schema import script_json_schema
from png_support import b64, png

REGISTRY = default_registry()
PNG = png()
OTHER = png(8, 5, seed=3)



def load(text: str) -> LoadResult:
    return load_script(text, lookup=REGISTRY.spec, check=REGISTRY.check_params)


def errors(text: str) -> list[dict[str, Any]]:
    result = load(text)
    assert not result.ok
    return [error.to_json() for error in result.errors]


def script_with(entry: str, steps: str = "  - log: hi\n") -> str:
    return f"steps:\n{steps}images:\n  icon: {entry}\n"


@pytest.fixture
def no_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load as if the vision extra were not installed."""
    monkeypatch.setattr(embedded, "pixel_check", lambda: None)


# --- helpers ----------------------------------------------------------------------------


def test_reference_names() -> None:
    assert embedded.reference_name("#save_button") == "save_button"
    assert embedded.reference_name("img/save.png") is None
    assert embedded.reference_name("#${name}") is None  # resolved when the step runs
    assert embedded.reference_name(3) is None


def test_base64_is_wrapped_at_76_columns_and_whitespace_is_ignored() -> None:
    data = bytes(range(256)) * 2
    wrapped = embedded.encode_base64(data)
    lines = wrapped.splitlines()
    assert all(len(line) == 76 for line in lines[:-1]) and 0 < len(lines[-1]) <= 76
    assert wrapped.endswith("\n")
    assert embedded.decode_base64(wrapped) == data
    assert embedded.decode_base64("  " + wrapped.replace("\n", " \r\n\t")) == data
    assert embedded.wrap_base64(wrapped.replace("\n", "")) == wrapped
    assert embedded.wrap_base64("") == ""
    with pytest.raises(ValueError, match="not valid base64"):
        embedded.decode_base64("iVBOR*w0K")


def test_png_size_reads_the_header() -> None:
    assert embedded.png_size(PNG) == (6, 4)
    with pytest.raises(ValueError, match="wrong signature"):
        embedded.png_size(b"GIF89a" + PNG)
    with pytest.raises(ValueError, match="no IHDR"):
        embedded.png_size(embedded.PNG_SIGNATURE + b"short")


# --- loading ----------------------------------------------------------------------------


def test_embedded_images_load_with_their_size_and_scale() -> None:
    wrapped = embedded.encode_base64(PNG).replace("\n", "\n      ")
    text = (f"steps:\n  - click: {{image: \"#icon\"}}\n"
            f"images:\n  icon:\n    png: |\n      {wrapped.rstrip()}\n"
            f"  other: {{png: {b64(OTHER)}, scale: 1.5}}\n")
    result = load(text)
    assert result.ok, result.errors
    script = result.script
    assert script is not None
    assert list(script.images) == ["icon", "other"]
    icon, other = script.images["icon"], script.images["other"]
    assert icon.data == PNG and (icon.width, icon.height) == (6, 4) and icon.scale is None
    assert icon.line == 4 and other.line == 9
    assert other.to_json() == {"name": "other", "format": "png", "bytes": len(OTHER),
                               "width": 8, "height": 5, "scale": 1.5}
    assert script.steps[0].params["image"] == "#icon"
    assert not [w for w in script.warnings if "top-level" in w.message]  # a known key
    assert "data=" not in repr(icon)  # never dumped in messages


@pytest.mark.parametrize(("entry", "message", "field"), [
    ("{png: 'iVBOR*w0K'}", "'png' is not valid base64", "png"),
    (f"{{png: {b64(b'GIF89a' + PNG)}}}", "not a PNG file", "png"),
    ("{png: 12}", "'png' must be base64 text", "png"),
    (f"{{png: {b64(PNG)}, scale: 0}}", "'scale' must be a positive number", "scale"),
    (f"{{png: {b64(PNG)}, scale: -1.5}}", "'scale' must be a positive number", "scale"),
    (f"{{png: {b64(PNG)}, scale: big}}", "'scale' must be a positive number", "scale"),
    (f"{{png: {b64(PNG)}, scale: true}}", "'scale' must be a positive number", "scale"),
    (f"{{png: {b64(PNG)}, dpi: 96}}", "unknown field(s) dpi", "dpi"),
])
def test_invalid_entries_are_reported_with_the_image_and_its_line(
        entry: str, message: str, field: str) -> None:
    [error] = errors(script_with(entry))
    assert error["code"] == "validation_failed"
    assert error["message"].startswith("image 'icon': ")
    assert message in error["message"]
    assert error["line"] == 4
    assert error["details"] == {"image": "icon", "field": field}


@pytest.mark.parametrize(("section", "message", "line"), [
    ("images: [a, b]\n", "'images' must be a mapping", 2),
    ("images:\n  icon: just text\n", "expected a mapping such as {png:", 3),
    ("images:\n  icon: {scale: 2}\n", "expected the image data under one of png", 3),
    (f"images:\n  2icon: {{png: {b64(PNG)}}}\n", "invalid name", 3),
])
def test_invalid_sections(section: str, message: str, line: int) -> None:
    [error] = errors(f"steps: []\n{section}")
    assert message in error["message"]
    assert error["line"] == line


def test_the_size_limits(monkeypatch: pytest.MonkeyPatch, no_vision: None) -> None:
    big = embedded.PNG_SIGNATURE + b"\0" * embedded.MAX_IMAGE_BYTES
    [error] = errors(script_with(f"{{png: {b64(big)}}}"))
    assert "over the limit of 1,048,576 bytes (1 MiB) per image" in error["message"]
    assert error["details"]["image"] == "icon"

    monkeypatch.setattr(embedded, "MAX_SCRIPT_BYTES", len(PNG) + len(OTHER) - 1)
    text = f"steps: []\nimages:\n  a: {{png: {b64(PNG)}}}\n  b: {{png: {b64(OTHER)}}}\n"
    [error] = errors(text)
    assert "over the limit" in error["message"] and "per script" in error["message"]
    assert error["line"] == 2
    assert error["details"] == {"field": "images", "bytes": len(PNG) + len(OTHER)}


def test_pixels_are_only_checked_with_the_vision_extra(
        monkeypatch: pytest.MonkeyPatch) -> None:
    broken = PNG[:40]  # the header is fine, the pixel data is cut
    monkeypatch.setattr(embedded, "pixel_check", lambda: None)
    assert load(script_with(f"{{png: {b64(broken)}}}")).ok
    monkeypatch.undo()
    pytest.importorskip("cv2")
    [error] = errors(script_with(f"{{png: {b64(broken)}}}"))
    assert "#icon is not a readable image" in error["message"]


def test_a_fully_transparent_image_is_refused_with_the_vision_extra() -> None:
    pytest.importorskip("cv2")
    [error] = errors(script_with(f"{{png: {b64(png(alpha=0))}}}"))
    assert "#icon is fully transparent" in error["message"]
    assert load(script_with(f"{{png: {b64(png(alpha=255))}}}")).ok


UNKNOWN_REFERENCES = [
    ("click: {image: \"#nope\"}", "image"),
    ("move: {image: \"#nope\"}", "image"),
    ("wait_image_in: \"#nope\"", "image"),
    ("drag: {from: {x: 1, y: 2}, to: {image: \"#nope\"}}", "to.image"),
    ("if: {when: {image_visible: \"#nope\"}, then: []}", "when.image_visible"),
    ("loop: {while: {image_visible: {image: \"#nope\"}}, do: []}",
     "while.image_visible.image"),
]


@pytest.mark.parametrize(("step", "param"), UNKNOWN_REFERENCES)
def test_an_unknown_reference_fails_to_load_with_the_step_and_parameter(
        step: str, param: str) -> None:
    [error] = errors(script_with(f"{{png: {b64(PNG)}}}", f"  - id: s\n    {step}\n"))
    assert error["code"] == "validation_failed"
    assert error["step_id"] == "s" and error["line"] == 2
    assert error["details"] == {"param": param, "image": "nope"}
    assert f"{param}: no embedded image named 'nope'; the script has icon" in error["message"]


def test_an_unknown_reference_without_images_says_so() -> None:
    [error] = errors("steps:\n  - id: s\n    click: {image: \"#icon\"}\n")
    assert error["message"] == ("image: no embedded image named 'icon'; the script has no "
                                "'images' section")


def test_a_reference_to_a_broken_entry_is_reported_once() -> None:
    [error] = errors(script_with("{png: 'iVBOR*w0K'}", "  - click: {image: \"#icon\"}\n"))
    assert error["details"]["image"] == "icon" and "base64" in error["message"]


def test_references_built_at_run_time_and_other_values_are_not_checked() -> None:
    text = ("vars: {icon: \"#later\"}\nsteps:\n"
            "  - wait_image_in: \"${icon}\"\n"
            "  - wait_image_in: \"#${icon}\"\n"
            "  - set: {name: x, value: {image: \"#not_an_image_parameter\"}}\n"
            "  - log: \"#hashtag\"\n")
    assert load(text).ok


# --- editing ----------------------------------------------------------------------------

BASE = """\
version: 1
name: Demo   # the name
steps:
  - id: w
    wait_image_in: img/icon.png
  - id: c
    click: {image: img/icon.png, threshold: 0.8}  # keep me
  - id: v
    if:
      when: {image_visible: img/other.png}
      then: []
# the end
"""


def edit(text: str, **meta: Any) -> str:
    return apply_edits(text, [SetMetaEdit(**meta)], lookup=REGISTRY.spec)


def test_set_meta_writes_images_as_literal_blocks_after_the_steps() -> None:
    text = edit(BASE, images={"icon": {"png": b64(PNG + PNG)}, "other": {
        "png": b64(OTHER), "scale": 1.25}})
    assert text.startswith(BASE.split("# the end")[0])
    tail = text.split("# the end\n")[1]
    lines = tail.splitlines()
    assert lines[0] == "images:"
    assert lines[1:3] == ["  icon:", "    png: |"]
    icon_body = lines[3:lines.index("  other:")]
    assert all(line.startswith("      ") for line in icon_body)
    assert [len(line) for line in icon_body[:-1]] == [6 + 76] * (len(icon_body) - 1)
    assert len(icon_body[-1]) <= 6 + 76
    assert lines[lines.index("  other:") + 1] == "    png: |"
    assert lines[-1] == "    scale: 1.25"
    result = load(text)
    assert result.ok and result.script is not None
    assert result.script.images["icon"].data == PNG + PNG
    assert result.script.images["other"].scale == 1.25


def test_set_meta_keeps_unchanged_entries_byte_for_byte() -> None:
    flat = f"{BASE}images:\n  icon: {{png: {b64(PNG)}}}   # flow, unwrapped\n"
    wrapped = embedded.encode_base64(PNG)  # same data, other line breaks
    again = edit(flat, images={"icon": {"png": wrapped}})
    assert again == flat
    changed = edit(flat, images={"icon": {"png": wrapped}, "new": {"png": b64(OTHER)}})
    assert changed.startswith(flat)
    assert "  new:\n    png: |\n" in changed


def test_set_meta_replaces_and_removes_images() -> None:
    both = edit(BASE, images={"icon": {"png": b64(PNG)}, "other": {"png": b64(OTHER)}})
    only = edit(both, images={"other": {"png": b64(OTHER)}})
    assert "  icon:" not in only and "  other:" in only
    replaced = edit(only, images={"other": {"png": b64(PNG)}})
    assert load(replaced).script.images["other"].data == PNG  # type: ignore[union-attr]
    assert edit(both, images={}).rstrip("\n").endswith("# the end")
    assert "images" not in edit(both, images={})


def test_set_meta_with_bad_data_saves_and_reports_it() -> None:
    text = edit(BASE, images={"icon": {"png": "not base64!"}})
    [error] = errors(text)
    assert "'png' is not valid base64" in error["message"]


def test_updating_a_parameter_to_a_reference_quotes_it() -> None:
    text = edit(BASE, images={"icon": {"png": b64(PNG)}})
    text = apply_edits(text, [UpdateEdit(step_id="w", params={"image": "#icon"})],
                       lookup=REGISTRY.spec)
    assert "    wait_image_in: '#icon'\n" in text
    result = load(text)
    assert result.ok and result.script is not None
    assert result.script.steps[0].params["image"] == "#icon"


def test_add_image_adds_one_entry_and_keeps_the_others() -> None:
    flat = f"{BASE}images:\n  icon: {{png: {b64(PNG)}}}   # flow, unwrapped\n"
    text = apply_edits(flat, [AddImageEdit(name="new", png=b64(OTHER), scale=1.5)],
                       lookup=REGISTRY.spec)
    assert text.startswith(flat)
    script = load(text).script
    assert script is not None and list(script.images) == ["icon", "new"]
    assert script.images["new"].data == OTHER and script.images["new"].scale == 1.5
    fresh = apply_edits(BASE, [AddImageEdit(name="icon", png=b64(PNG))],
                        lookup=REGISTRY.spec)
    assert fresh.startswith(BASE) and "images:\n  icon:\n    png: |\n" in fresh


@pytest.mark.parametrize(("edit_", "reason"), [
    (AddImageEdit(name="icon", png=b64(OTHER)), "already has an image named 'icon'"),
    (AddImageEdit(name="1st", png=b64(OTHER)), "invalid image name '1st'"),
    (AddImageEdit(name="bad", png="not base64!"), "not valid base64"),
    (AddImageEdit(name="gif", png=b64(b"GIF89a" + PNG)), "not a PNG file"),
    (RenameImageEdit(name="nope", new_name="x"), "no embedded image named 'nope'"),
    (RenameImageEdit(name="icon", new_name="other"), "already has an image named 'other'"),
    (RenameImageEdit(name="icon", new_name="a b"), "invalid image name 'a b'"),
    (DeleteImageEdit(name="nope"), "no embedded image named 'nope'"),
])
def test_image_edits_that_cannot_apply(edit_: Any, reason: str) -> None:
    text = edit(BASE, images={"icon": {"png": b64(PNG)}, "other": {"png": b64(OTHER)}})
    with pytest.raises(EditError) as info:
        apply_edits(text, [edit_], lookup=REGISTRY.spec)
    assert reason in info.value.message and info.value.index == 0


def test_add_image_overwrites_only_when_asked() -> None:
    text = edit(BASE, images={"icon": {"png": b64(PNG)}})
    text = apply_edits(text, [AddImageEdit(name="icon", png=b64(OTHER), overwrite=True)],
                       lookup=REGISTRY.spec)
    assert load(text).script.images["icon"].data == OTHER  # type: ignore[union-attr]


def test_rename_image_renames_the_entry_and_its_references() -> None:
    packed = rewrite_images(
        BASE, {"img/icon.png": "#icon", "img/other.png": "#other"}, lookup=REGISTRY.spec,
        add={"icon": {"png": b64(PNG)}, "other": {"png": b64(OTHER)}})
    text = apply_edits(packed, [RenameImageEdit(name="icon", new_name="button")],
                       lookup=REGISTRY.spec)
    assert '"#icon"' not in text and text.count('"#button"') == 2
    assert "  button:\n    png: |\n" in text
    script = load(text).script
    assert script is not None and list(script.images) == ["button", "other"]
    assert script.images["button"].data == PNG
    assert script.find("c") is not None
    assert script.find("c").params["image"] == "#button"  # type: ignore[union-attr]
    assert "threshold: 0.8}" in text and "# keep me" in text


def test_delete_image_removes_the_entry_and_then_the_section() -> None:
    text = edit(BASE, images={"icon": {"png": b64(PNG)}, "other": {"png": b64(OTHER)}})
    one = apply_edits(text, [DeleteImageEdit(name="icon")], lookup=REGISTRY.spec)
    assert "  icon:" not in one and "  other:" in one
    none = apply_edits(one, [DeleteImageEdit(name="other")], lookup=REGISTRY.spec)
    assert "images" not in none and none.startswith(BASE.split("# the end")[0])


def test_rewrite_images_packs_and_unpacks_without_touching_the_rest() -> None:
    packed = rewrite_images(
        BASE, {"img/icon.png": "#icon", "img/other.png": "#other"}, lookup=REGISTRY.spec,
        add={"icon": {"png": b64(PNG)}, "other": {"png": b64(OTHER)}})
    head = packed.split("images:\n")[0]
    assert head == (BASE.replace("img/icon.png", "\"#icon\"")
                    .replace("img/other.png", "\"#other\"")
                    .replace("0.8}  # keep me", "0.8}       # keep me"))
    assert "id:" not in packed.replace("  - id: ", "")  # no id was added
    result = load(packed)
    assert result.ok, result.errors
    unpacked = rewrite_images(packed, {"#icon": "img/icon.png", "#other": "img/other.png"},
                              lookup=REGISTRY.spec, remove_section=True)
    assert unpacked == BASE


def test_rewrite_images_adds_to_an_existing_section() -> None:
    text = f"{BASE}images:\n  old: {{png: {b64(OTHER)}}}\n"
    packed = rewrite_images(text, {"img/icon.png": "#icon"}, lookup=REGISTRY.spec,
                            add={"icon": {"png": b64(PNG)}})
    assert packed.startswith(text.split("images:")[0].replace("img/icon.png", "\"#icon\"")
                             .replace("0.8}  # keep me", "0.8}       # keep me"))
    assert f"  old: {{png: {b64(OTHER)}}}\n  icon:\n    png: |\n" in packed


def test_rewrite_images_keeps_the_short_form_and_unknown_steps() -> None:
    text = "steps:\n  - wait_image_in: a.png\n  - mystery: {image: a.png}\n"
    out = rewrite_images(text, {"a.png": "#a"}, lookup=REGISTRY.spec)
    assert out == "steps:\n  - wait_image_in: \"#a\"\n  - mystery: {image: a.png}\n"


# --- schema -----------------------------------------------------------------------------


def test_the_json_schema_describes_the_images_section() -> None:
    schema = script_json_schema(REGISTRY)["properties"]["images"]
    entry = schema["additionalProperties"]
    assert entry["required"] == ["png"]
    assert entry["properties"]["png"]["contentEncoding"] == "base64"
    assert entry["properties"]["scale"]["exclusiveMinimum"] == 0
    assert schema["propertyNames"]["pattern"] == embedded.NAME_PATTERN.pattern
