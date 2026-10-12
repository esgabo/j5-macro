"""Minimal type-hint driven JSON codec for frozen payload dataclasses.

Supported field types: ``str``, ``int``, ``float``, ``bool``, ``Any``, ``X | None``,
``list[T]``, ``dict[str, T]``, ``Literal[...]``, ``StrEnum``, nested dataclasses and
unions of dataclasses discriminated by a single-valued ``Literal`` field (e.g. ``op``).

Unknown fields are ignored. Optional fields (``X | None``) that are None are omitted
by ``to_json``. A field whose JSON name differs from the Python name (``from`` is a
keyword) declares it with ``field(metadata=wire_name(...))``.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from enum import Enum
from typing import Any, ClassVar, Literal, Self, Union, get_args, get_origin, get_type_hints

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo, JsonObject

_WIRE_KEY = "j5_wire_name"


class PayloadError(ValueError):
    """A payload or result does not match its schema. ``error.code`` is validation_failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.error = ErrorInfo(code=ErrorCode.VALIDATION_FAILED, message=message)


def wire_name(name: str) -> dict[str, str]:
    """Field metadata that serializes a field under ``name``:
    ``start: int = field(default=0, metadata=wire_name("from"))``."""
    return {_WIRE_KEY: name}


class Payload:
    """Mixin for payload dataclasses: ``from_json`` validates, ``to_json`` serializes."""

    __slots__ = ()

    _root: ClassVar[str] = "payload"

    @classmethod
    def from_json(cls, data: Any, path: str | None = None) -> Self:
        result: Self = _decode_dataclass(cls, data, path or cls._root)
        return result

    def to_json(self) -> JsonObject:
        return _encode_dataclass(self)

    def check(self, path: str) -> None:
        """Cross-field checks; override and raise :class:`PayloadError`."""


# --- decoding ---------------------------------------------------------------------------

_JSON_NAMES: dict[type, str] = {
    str: "string", int: "integer", float: "number", bool: "boolean",
    list: "array", dict: "object", type(None): "null",
}


def _json_type(value: Any) -> str:
    return _JSON_NAMES.get(type(value), type(value).__name__)


def _fail(path: str, expected: str, value: Any) -> PayloadError:
    return PayloadError(f"{path}: expected {expected}, got {_json_type(value)}")


def _hints(cls: type) -> dict[str, Any]:
    cached = _HINTS.get(cls)
    if cached is None:
        cached = get_type_hints(cls)
        _HINTS[cls] = cached
    return cached


_HINTS: dict[type, dict[str, Any]] = {}


def _wire(f: dataclasses.Field[Any]) -> str:
    return str(f.metadata.get(_WIRE_KEY, f.name))


def _is_optional(tp: Any) -> bool:
    return _is_union(tp) and type(None) in get_args(tp)


def _is_union(tp: Any) -> bool:
    return get_origin(tp) in (Union, types.UnionType)


def _decode_dataclass(cls: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise _fail(path, "object", data)
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        key = _wire(f)
        tp = hints[f.name]
        if key in data:
            kwargs[f.name] = _decode(tp, data[key], f"{path}.{key}")
        elif _is_optional(tp) and f.default is dataclasses.MISSING:
            kwargs[f.name] = None
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise PayloadError(f"{path}.{key}: missing required field")
    instance = cls(**kwargs)
    if isinstance(instance, Payload):
        instance.check(path)
    return instance


def _decode(tp: Any, value: Any, path: str) -> Any:
    if tp is Any:
        return value
    origin = get_origin(tp)
    if origin is Literal:
        options = get_args(tp)
        if any(value == opt and type(value) is type(opt) for opt in options):
            return value
        raise PayloadError(f"{path}: expected one of {', '.join(map(str, options))}")
    if _is_union(tp):
        members = [m for m in get_args(tp) if m is not type(None)]
        if value is None:
            if len(members) < len(get_args(tp)):
                return None
            raise _fail(path, "object", value)
        if len(members) == 1:
            return _decode(members[0], value, path)
        return _decode_tagged(members, value, path)
    if origin is list:
        if not isinstance(value, list):
            raise _fail(path, "array", value)
        (item,) = get_args(tp)
        return [_decode(item, v, f"{path}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise _fail(path, "object", value)
        _, item = get_args(tp)
        return {k: _decode(item, v, f"{path}.{k}") for k, v in value.items()}
    if tp is bool:
        if not isinstance(value, bool):
            raise _fail(path, "boolean", value)
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail(path, "integer", value)
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _fail(path, "number", value)
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise _fail(path, "string", value)
        return value
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError:
            names = ", ".join(str(m.value) for m in tp)
            raise PayloadError(f"{path}: expected one of {names}") from None
    if dataclasses.is_dataclass(tp):
        return _decode_dataclass(typing.cast(type, tp), value, path)
    raise TypeError(f"unsupported payload type at {path}: {tp!r}")


def _tag_of(cls: type) -> dict[str, Any]:
    """Single-valued Literal fields of a dataclass: the candidates for a discriminator."""
    tags: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        tp = _hints(cls)[f.name]
        if get_origin(tp) is Literal and len(get_args(tp)) == 1:
            tags[_wire(f)] = get_args(tp)[0]
    return tags


def _decode_tagged(members: list[Any], value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise _fail(path, "object", value)
    if not all(dataclasses.is_dataclass(m) for m in members):
        raise TypeError(f"unsupported union at {path}: only dataclass unions are allowed")
    tags = [_tag_of(m) for m in members]
    common = set(tags[0]).intersection(*tags[1:])
    if len(common) != 1:
        raise TypeError(f"union at {path} has no single discriminator field")
    key = common.pop()
    if key not in value:
        raise PayloadError(f"{path}.{key}: missing required field")
    for member, tag in zip(members, tags, strict=True):
        if value[key] == tag[key]:
            return _decode_dataclass(member, value, path)
    options = ", ".join(str(t[key]) for t in tags)
    raise PayloadError(f"{path}.{key}: expected one of {options}")


# --- encoding ---------------------------------------------------------------------------


def _encode_dataclass(obj: Any) -> JsonObject:
    hints = _hints(type(obj))
    data: JsonObject = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if value is None and _is_optional(hints[f.name]):
            continue
        data[_wire(f)] = _encode(value)
    return data


def _encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _encode_dataclass(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value
