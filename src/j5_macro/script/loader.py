"""YAML script loader (docs/contract.md, "Script format and step registry").

Turns script text into a :class:`~j5_macro.script.model.Script`, keeping the 1-based
line of every step, assigning stable ids and collecting every problem it finds instead
of stopping at the first one. Embedded images (the ``images`` section) are decoded and
checked here, and every literal ``#name`` reference must name one of them. The loader
knows nothing about the step registry: it gets
step specs through a :data:`~j5_macro.steps.spec.StepLookup` and optional cross-field
checks through a :data:`CheckFn`.
"""

from __future__ import annotations

import datetime
import keyword
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.script import images as embedded
from j5_macro.script.model import (
    EmbeddedImage,
    OnError,
    Script,
    ScriptParam,
    Step,
    StepPath,
    StoreAs,
)
from j5_macro.script.params import PARAM_FIELDS, PARAM_TYPES
from j5_macro.steps.spec import (
    ParamProblem,
    StepLookup,
    StepSpec,
    parse_duration,
    validate_params,
)

CheckFn = Callable[[str, dict[str, Any]], list[ParamProblem]]
"""Cross-field checks for a step type: ``check(type, defaulted_params)``."""

SUPPORTED_VERSION = 1
ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_TOP_LEVEL = ("version", "name", "input", "capture_scale", "params", "vars", "steps",
              "images")
"""Top-level keys, in the order the editor places a new section."""
_COMMON = ("id", "break", "timeout", "on_error", "input", "as")
_ON_ERROR = ("stop", "continue", "retry", "pause")


@dataclass(frozen=True)
class LoadResult:
    """The loaded script (None when there are errors) and the errors found."""

    script: Script | None
    errors: list[ErrorInfo]

    @property
    def ok(self) -> bool:
        return self.script is not None


def load_script(
    text: str,
    *,
    lookup: StepLookup,
    check: CheckFn | None = None,
    source_path: str | None = None,
) -> LoadResult:
    """Parse and validate script ``text``."""
    try:
        data = YAML(typ="rt").load(text)
    except YAMLError as exc:
        return LoadResult(None, [_parse_error(exc)])
    return _Loader(lookup, check).load(data, text, source_path)


def load_file(
    path: str | os.PathLike[str],
    *,
    lookup: StepLookup,
    check: CheckFn | None = None,
) -> LoadResult:
    """Read a UTF-8 script file and load it; IO errors become ``script_not_found``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        info = ErrorInfo(
            code=ErrorCode.PARSE_ERROR.value,
            message=f"script is not valid UTF-8: {exc.reason}",
            details={"path": str(path)},
        )
        return LoadResult(None, [info])
    except OSError as exc:
        info = ErrorInfo(
            code=ErrorCode.SCRIPT_NOT_FOUND.value,
            message=f"cannot open script {str(path)!r}: {exc.strerror or exc}",
            details={"path": str(path)},
        )
        return LoadResult(None, [info])
    return load_script(text, lookup=lookup, check=check, source_path=str(path))


# --- internals --------------------------------------------------------------------------


def _parse_error(exc: YAMLError) -> ErrorInfo:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    problem = getattr(exc, "problem", None)
    context = getattr(exc, "context", None)
    reason = " ".join(str(part) for part in (context, problem) if part) or str(exc)
    line: int | None = None
    details: dict[str, Any] | None = None
    if mark is not None:
        line = mark.line + 1
        details = {"column": mark.column + 1}
    return ErrorInfo(
        code=ErrorCode.PARSE_ERROR.value,
        message=f"invalid YAML: {reason}",
        line=line,
        details=details,
    )


def _plain(value: Any) -> Any:
    """Convert ruamel round-trip containers and scalars to plain Python values."""
    if isinstance(value, Mapping):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and type(value).__name__ == "ScalarBoolean":
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _of_type(value: Any, kind: str) -> bool:
    """Whether a param default is natively of ``kind`` (no conversion from strings)."""
    if isinstance(value, bool):
        return kind == "boolean"
    if isinstance(value, int):
        return kind in ("integer", "number")
    if isinstance(value, float):
        return kind == "number" and value == value and abs(value) != float("inf")
    return isinstance(value, str) and kind == "string"


def _a(kind: str) -> str:
    return f"an {kind}" if kind[0] in "aeiou" else f"a {kind}"


def _key_line(mapping: Any, key: Any, default: int | None = None) -> int | None:
    try:
        return int(mapping.lc.key(key)[0]) + 1
    except (AttributeError, KeyError, TypeError):
        return default


def _item_line(seq: Any, index: int, default: int | None = None) -> int | None:
    try:
        return int(seq.lc.item(index)[0]) + 1
    except (AttributeError, KeyError, TypeError):
        return default


@dataclass(slots=True)
class _Problem:
    message: str
    code: ErrorCode = ErrorCode.VALIDATION_FAILED
    details: dict[str, Any] | None = None


@dataclass(eq=False, slots=True)
class _Node:
    """A step being built; becomes a :class:`Step` once ids are assigned."""

    line: int | None
    path: StepPath
    type: str | None = None
    explicit_id: str | None = None
    bad_id: bool = False
    id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    breakpoint: bool = False
    on_error: str = "stop"
    timeout: float | None = None
    input: dict[str, Any] | None = None
    store_as: StoreAs | None = None
    blocks: dict[str, list[_Node]] = field(default_factory=dict)
    problems: list[_Problem] = field(default_factory=list)

    def problem(self, message: str, **details: Any) -> None:
        self.problems.append(_Problem(message, details=details or None))

    def build(self) -> Step:
        assert self.id is not None and self.type is not None
        return Step(
            id=self.id,
            type=self.type,
            params=self.params,
            path=self.path,
            line=self.line,
            breakpoint=self.breakpoint,
            on_error=cast(OnError, self.on_error),
            timeout=self.timeout,
            input=self.input,
            store_as=self.store_as,
            blocks={slot: [c.build() for c in children]
                    for slot, children in self.blocks.items()},
        )


class _Loader:
    def __init__(self, lookup: StepLookup, check: CheckFn | None) -> None:
        self.lookup = lookup
        self.check = check
        self.errors: list[ErrorInfo] = []
        self.warnings: list[ErrorInfo] = []
        self.nodes: list[_Node] = []  # every step, in document order
        self.image_names: set[str] = set()
        """Every name declared in ``images``, valid or not (a broken entry is reported
        once, not again at each reference to it)."""

    def error(self, message: str, line: int | None, **details: Any) -> None:
        self.errors.append(ErrorInfo(
            code=ErrorCode.VALIDATION_FAILED.value, message=message, line=line,
            details=details or None,
        ))

    # --- top level ----------------------------------------------------------------------

    def load(self, data: Any, text: str, source_path: str | None) -> LoadResult:
        if not isinstance(data, Mapping):
            self.error("the script must be a mapping with a 'steps' list", 1)
            return LoadResult(None, self.errors)

        for key in data:
            if key not in _TOP_LEVEL:
                self.warnings.append(ErrorInfo(
                    code=ErrorCode.VALIDATION_FAILED.value,
                    message=f"unknown top-level key {str(key)!r} is ignored",
                    line=_key_line(data, key), details={"key": str(key)},
                ))

        version = data.get("version", SUPPORTED_VERSION)
        if (not isinstance(version, int) or isinstance(version, bool)
                or version != SUPPORTED_VERSION):
            self.error(f"unsupported script version {_plain(version)!r}; "
                       f"only {SUPPORTED_VERSION} is supported",
                       _key_line(data, "version"), field="version")
            version = SUPPORTED_VERSION

        default_name = Path(source_path).stem if source_path else "script"
        name = data.get("name", default_name)
        if not isinstance(name, str):
            self.error("'name' must be a string", _key_line(data, "name"), field="name")
            name = default_name

        script_input = self.mapping(data, "input")
        script_vars = self.mapping(data, "vars")
        script_params = self.params(data, script_vars)
        script_images = self.images(data)
        capture_scale = self.capture_scale(data)

        steps: list[_Node] = []
        if "steps" not in data:
            self.error("missing 'steps' list", None, field="steps")
        elif not isinstance(data["steps"], list):
            self.error("'steps' must be a list of steps", _key_line(data, "steps"),
                       field="steps")
        else:
            steps = self.steps(data["steps"], ())

        self.assign_ids()
        for node in self.nodes:
            for p in node.problems:
                self.errors.append(ErrorInfo(
                    code=p.code.value, message=p.message,
                    step_id=node.id,
                    line=node.line, details=p.details,
                ))

        if self.errors:
            return LoadResult(None, _by_line(self.errors))
        script = Script(
            name=str(name),
            steps=[node.build() for node in steps],
            version=int(version),
            input=script_input,
            params=script_params,
            vars=script_vars,
            images=script_images,
            capture_scale=capture_scale,
            source_path=source_path,
            text=text,
            warnings=_by_line(self.warnings),
        )
        return LoadResult(script, [])

    def capture_scale(self, data: Any) -> float | None:
        value = data.get("capture_scale")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float) \
                or not 0 < value < float("inf"):
            self.error("'capture_scale' must be a positive number: the display scale the "
                       "templates and pixel offsets were taken at, e.g. 1.5 for 150 %",
                       _key_line(data, "capture_scale"), field="capture_scale")
            return None
        return float(value)

    def mapping(self, data: Any, key: str) -> dict[str, Any]:
        value = data.get(key)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            self.error(f"{key!r} must be a mapping", _key_line(data, key), field=key)
            return {}
        return cast(dict[str, Any], _plain(value))

    # --- params -------------------------------------------------------------------------

    def params(self, data: Any, script_vars: dict[str, Any]) -> dict[str, ScriptParam]:
        raw = data.get("params")
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            self.error("'params' must be a mapping of param names", _key_line(data, "params"),
                       field="params")
            return {}
        result: dict[str, ScriptParam] = {}
        for key, spec in raw.items():
            line = _key_line(raw, key)
            name = str(key)
            if not name.isidentifier():
                self.error(f"invalid param name {name!r}: use letters, digits and _",
                           line, param=name)
                continue
            if name in script_vars:
                self.error(f"'{name}' is both a param and a var", line, param=name)
                continue
            param = self.param(name, spec, line)
            if param is not None:
                result[name] = param
        return result

    def param(self, name: str, spec: Any, line: int | None) -> ScriptParam | None:
        if spec is None:
            return ScriptParam(name=name, line=line)
        if not isinstance(spec, Mapping):
            self.error(f"param '{name}' must be a mapping such as {{type: string, "
                       "description: ...}}, or empty for a required string", line, param=name)
            return None
        values = cast(dict[str, Any], _plain(spec))
        unknown = sorted(set(values) - set(PARAM_FIELDS))
        if unknown:
            self.error(f"param '{name}': unknown field(s) {', '.join(unknown)}; "
                       f"expected {', '.join(PARAM_FIELDS)}", line, param=name)
            return None
        kind = values.get("type", "string")
        if kind not in PARAM_TYPES:
            self.error(f"param '{name}': type must be one of {', '.join(PARAM_TYPES)}",
                       line, param=name)
            return None
        description = values.get("description")
        if description is not None and not isinstance(description, str):
            self.error(f"param '{name}': description must be a string", line, param=name)
            return None
        secret = values.get("secret", False)
        if not isinstance(secret, bool):
            self.error(f"param '{name}': secret must be true or false", line, param=name)
            return None
        param = ScriptParam(name=name, type=kind, description=description, secret=secret,
                            line=line)
        if "default" not in values:
            return param
        if secret:
            self.error(f"param '{name}' is secret and cannot have a default: pass it "
                       "when running the script", line, param=name)
            return None
        default = values["default"]
        if not _of_type(default, kind):
            self.error(f"param '{name}': the default must be {_a(kind)}", line, param=name)
            return None
        return ScriptParam(name=name, type=kind, description=description, default=default,
                           secret=False, line=line)

    # --- images -------------------------------------------------------------------------

    def images(self, data: Any) -> dict[str, EmbeddedImage]:
        raw = data.get("images")
        if raw is None:
            return {}
        section_line = _key_line(data, "images")
        if not isinstance(raw, Mapping):
            self.error("'images' must be a mapping of image names to {png: <base64>}",
                       section_line, field="images")
            return {}
        result: dict[str, EmbeddedImage] = {}
        total = 0
        for key, entry in raw.items():
            name = str(key)
            self.image_names.add(name)
            image = self.image(name, entry, _key_line(raw, key, section_line))
            if image is not None:
                result[name] = image
                total += len(image.data)
        if total > embedded.MAX_SCRIPT_BYTES:
            self.error(f"the embedded images add up to {total:,} bytes once decoded, over "
                       f"the limit of {embedded.MAX_SCRIPT_BYTES:,} bytes (8 MiB) per "
                       "script; keep some of them as files", section_line,
                       field="images", bytes=total)
        return result

    def image(self, name: str, entry: Any, line: int | None) -> EmbeddedImage | None:
        def fail(message: str, **details: Any) -> None:
            self.error(f"image '{name}': {message}", line, image=name, **details)

        if not embedded.NAME_PATTERN.match(name):
            fail("invalid name: use letters, digits, '_' or '-', starting with a letter "
                 "or '_'")
            return None
        if not isinstance(entry, Mapping):
            fail("expected a mapping such as {png: <base64 of the PNG file>}")
            return None
        values = cast(dict[str, Any], _plain(entry))
        unknown = sorted(set(values) - set(embedded.ENTRY_FIELDS))
        if unknown:
            fail(f"unknown field(s) {', '.join(unknown)}; expected "
                 f"{', '.join(embedded.ENTRY_FIELDS)}", field=unknown[0])
            return None
        formats = [key for key in embedded.FORMATS if key in values]
        if len(formats) != 1:
            fail(f"expected the image data under one of {', '.join(embedded.FORMATS)}")
            return None
        kind = formats[0]
        text = values[kind]
        if not isinstance(text, str):
            fail(f"'{kind}' must be base64 text", field=kind)
            return None
        scale = values.get("scale")
        if scale is not None and (isinstance(scale, bool) or not isinstance(scale, int | float)
                                  or not 0 < scale < float("inf")):
            fail("'scale' must be a positive number (the display scale the image was "
                 "captured at, e.g. 1.5)", field="scale")
            return None
        try:
            data = embedded.decode_base64(text)
        except ValueError as exc:
            fail(f"'{kind}' is {exc}", field=kind)
            return None
        try:
            width, height = embedded.check_png(data, f"#{name}")
        except ValueError as exc:
            fail(str(exc), field=kind)
            return None
        return EmbeddedImage(name=name, format=kind, data=data, width=width, height=height,
                             scale=None if scale is None else float(scale), line=line)

    def references(self, node: _Node, spec: StepSpec) -> None:
        """Every literal ``#name`` in the step's parameters must name an embedded image."""
        for mapping, key, param in embedded.image_slots(node.params, spec):
            name = embedded.reference_name(mapping[key])
            if name is None or name in self.image_names:
                continue
            hint = (f"; the script has {', '.join(sorted(self.image_names))}"
                    if self.image_names else "; the script has no 'images' section")
            node.problem(f"{param}: no embedded image named {name!r}{hint}",
                         param=param, image=name)

    # --- steps --------------------------------------------------------------------------

    def steps(self, seq: list[Any], base: StepPath) -> list[_Node]:
        nodes: list[_Node] = []
        for index, raw in enumerate(seq):
            line = _item_line(seq, index)
            node = self.step(raw, line, (*base, index))
            if node is not None:
                nodes.append(node)
        return nodes

    def step(self, raw: Any, line: int | None, path: StepPath) -> _Node | None:
        if not isinstance(raw, Mapping):
            self.error("a step must be a mapping with a step type key", line)
            return None
        node = _Node(line=line, path=path)
        self.nodes.append(node)
        self.common(node, raw)

        type_keys = [str(k) for k in raw if k not in _COMMON]
        if not type_keys:
            node.problems.append(_Problem("step has no type key"))
            return node
        if len(type_keys) > 1:
            node.problems.append(_Problem(
                f"step has several type keys ({', '.join(type_keys)}); use one per step",
                details={"types": type_keys},
            ))
            return node

        node.type = type_keys[0]
        spec = self.lookup(node.type)
        if spec is None:
            node.problems.append(_Problem(
                f"unknown step type {node.type!r}", code=ErrorCode.UNKNOWN_STEP_TYPE,
                details={"type": node.type},
            ))
            return node
        raw_key = next(k for k in raw if str(k) == node.type)
        self.body(node, spec, raw[raw_key])
        return node

    def common(self, node: _Node, raw: Mapping[Any, Any]) -> None:
        if "id" in raw:
            value = raw["id"]
            if isinstance(value, str) and ID_PATTERN.match(value):
                node.explicit_id = str(value)
            else:
                node.bad_id = True
                node.problem(f"invalid id {_plain(value)!r}: use letters, digits, '_' "
                             "or '-', starting with a letter or '_'", field="id")
        if "break" in raw:
            value = raw["break"]
            if isinstance(value, bool):
                node.breakpoint = bool(value)
            else:
                node.problem("'break' must be true or false", field="break")
        if "timeout" in raw and raw["timeout"] is not None:
            try:
                node.timeout = parse_duration(_plain(raw["timeout"])).seconds
            except ValueError as exc:
                node.problem(f"invalid 'timeout': {exc}", field="timeout")
        if "on_error" in raw:
            value = raw["on_error"]
            if value in _ON_ERROR:
                node.on_error = str(value)
            else:
                node.problem(f"'on_error' must be one of {', '.join(_ON_ERROR)}",
                             field="on_error")
        if "input" in raw and raw["input"] is not None:
            if isinstance(raw["input"], Mapping):
                node.input = _plain(raw["input"])
            else:
                node.problem("'input' must be a mapping", field="input")
        if "as" in raw:
            node.store_as = self.store_as(node, raw["as"])

    @staticmethod
    def store_as(node: _Node, value: Any) -> StoreAs | None:
        """The ``as`` field: ``NAME`` or ``{name: NAME, secret: true}``."""
        value, secret = _plain(value), False
        if isinstance(value, Mapping):
            unknown = sorted(str(k) for k in value if k not in ("name", "secret"))
            if unknown:
                node.problem(f"'as': unknown field(s) {', '.join(unknown)}; expected name "
                             "and secret", field="as")
                return None
            name, secret = value.get("name"), value.get("secret", False)
        else:
            name = value
        if not is_variable_name(name):
            node.problem(f"'as' needs a variable name (letters, digits and _, not starting "
                         f"with a digit), got {name!r}", field="as")
            return None
        if not isinstance(secret, bool):
            node.problem("'as': secret must be true or false", field="as")
            return None
        return StoreAs(name=str(name), secret=secret)

    def body(self, node: _Node, spec: StepSpec, value: Any) -> None:
        assert node.type is not None
        params: dict[str, Any] = {}
        if value is None:
            pass
        elif isinstance(value, Mapping):
            for key, item in value.items():
                name = str(key)
                if name in spec.blocks:
                    node.blocks[name] = self.block(node, spec, name, item)
                else:
                    params[name] = _plain(item)
        elif isinstance(value, list):
            node.problem(f"parameters of {node.type!r} must be a mapping")
            return
        elif spec.shorthand is None:
            node.problem(f"{node.type!r} has no short form; write its parameters as a "
                         "mapping")
            return
        else:
            params[spec.shorthand] = _plain(value)

        for slot in spec.required_blocks:
            if slot not in node.blocks:
                node.problem(f"missing block {slot!r}", block=slot)

        defaulted, problems = validate_params(spec, params)
        if not problems and self.check is not None:
            problems = self.check(node.type, defaulted)
        for p in problems:
            node.problem(f"{p.param}: {p.message}", param=p.param, **(p.details or {}))
        node.params = defaulted
        self.references(node, spec)

    def block(self, node: _Node, spec: StepSpec, slot: str, value: Any) -> list[_Node]:
        if value is None:
            return []
        if not isinstance(value, list):
            node.problem(f"block {slot!r} must be a list of steps", block=slot)
            return []
        return self.steps(value, (*node.path, spec.blocks.index(slot)))

    # --- ids ----------------------------------------------------------------------------

    def assign_ids(self) -> None:
        taken: dict[str, int | None] = {}
        for node in self.nodes:
            sid = node.explicit_id
            if sid is None:
                continue
            node.id = sid
            if sid in taken:
                first = taken[sid]
                node.problem(f"duplicate id {sid!r} on lines {first} and {node.line}",
                             field="id", first_line=first)
            else:
                taken[sid] = node.line

        # The n-th step of a type (counting steps with explicit ids too) gets ``<type>_n``,
        # so adding an id to one step never renames the generated ids of the others.
        position: dict[str, int] = {}
        for node in self.nodes:
            if node.type is None:
                continue
            position[node.type] = position.get(node.type, 0) + 1
            if node.explicit_id is not None:
                continue
            n = position[node.type]
            candidate = f"{node.type}_{n}"
            suffix = 1
            while candidate in taken:
                suffix += 1
                candidate = f"{node.type}_{n}_{suffix}"
            taken[candidate] = node.line
            node.id = candidate
            if not node.bad_id:
                self.warnings.append(ErrorInfo(
                    code=ErrorCode.VALIDATION_FAILED.value,
                    message=f"generated id {candidate!r}, add an explicit id to keep "
                            "breakpoints stable",
                    step_id=candidate, line=node.line, details={"generated_id": True},
                ))


def is_variable_name(name: Any) -> bool:
    """Whether ``name`` can name a variable that expressions read: an identifier that
    is not a Python keyword."""
    return isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name)


def _by_line(infos: list[ErrorInfo]) -> list[ErrorInfo]:
    """Stable sort by line; entries without a line go first."""
    return sorted(infos, key=lambda info: info.line or 0)
