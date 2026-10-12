"""Message envelope and JSON-lines encoding.

Every message is a single-line JSON object with the common fields defined in
docs/contract.md ("Transport and message format"). Unknown fields are
ignored on decode so that newer engines never break older clients.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

JsonObject = dict[str, Any]


class ProtocolError(ValueError):
    """Raised when a line cannot be decoded into a protocol message."""


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Error shape used in responses and events: ``{code, message, ...}``."""

    code: str
    message: str
    step_id: str | None = None
    line: int | None = None
    details: JsonObject | None = None

    def to_json(self) -> JsonObject:
        data: JsonObject = {"code": self.code, "message": self.message}
        if self.step_id is not None:
            data["step_id"] = self.step_id
        if self.line is not None:
            data["line"] = self.line
        if self.details is not None:
            data["details"] = self.details
        return data

    @classmethod
    def from_json(cls, data: JsonObject) -> ErrorInfo:
        return cls(
            code=str(data["code"]),
            message=str(data.get("message", "")),
            step_id=data.get("step_id"),
            line=data.get("line"),
            details=data.get("details"),
        )


@dataclass(frozen=True, slots=True)
class Command:
    """Client -> engine. ``id`` correlates the command with its response."""

    type: str
    id: str
    payload: JsonObject = field(default_factory=dict)
    v: int = PROTOCOL_VERSION

    def to_json(self) -> JsonObject:
        return {"v": self.v, "kind": "command", "type": self.type, "id": self.id,
                "payload": self.payload}


@dataclass(frozen=True, slots=True)
class Response:
    """Engine -> client answer to exactly one command."""

    type: str
    id: str
    ok: bool
    result: JsonObject | None = None
    error: ErrorInfo | None = None
    v: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.ok and self.error is not None:
            raise ValueError("a successful response cannot carry an error")
        if not self.ok and self.error is None:
            raise ValueError("a failed response must carry an error")

    def to_json(self) -> JsonObject:
        data: JsonObject = {"v": self.v, "kind": "response", "type": self.type, "id": self.id,
                            "ok": self.ok}
        if self.ok:
            data["result"] = self.result or {}
        else:
            assert self.error is not None
            data["error"] = self.error.to_json()
        return data


@dataclass(frozen=True, slots=True)
class Event:
    """Engine -> client notification. ``run_id`` is None for engine-level events."""

    type: str
    seq: int
    ts: str
    t_ms: int
    run_id: str | None = None
    payload: JsonObject = field(default_factory=dict)
    v: int = PROTOCOL_VERSION

    def to_json(self) -> JsonObject:
        return {"v": self.v, "kind": "event", "type": self.type, "run_id": self.run_id,
                "seq": self.seq, "ts": self.ts, "t_ms": self.t_ms, "payload": self.payload}


Message = Command | Response | Event


def encode(message: Message) -> str:
    """Serialize a message as one JSON line (no trailing newline)."""
    return json.dumps(message.to_json(), ensure_ascii=False, separators=(",", ":"))


def decode(line: str) -> Message:
    """Parse one JSON line into a message, ignoring unknown fields."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("a message must be a JSON object")

    try:
        kind = data["kind"]
        version = int(data["v"])
        if kind == "command":
            return Command(type=data["type"], id=data["id"],
                           payload=data.get("payload") or {}, v=version)
        if kind == "response":
            error = data.get("error")
            return Response(type=data["type"], id=data["id"], ok=bool(data["ok"]),
                            result=data.get("result"),
                            error=ErrorInfo.from_json(error) if error else None, v=version)
        if kind == "event":
            return Event(type=data["type"], seq=int(data["seq"]), ts=data["ts"],
                         t_ms=int(data["t_ms"]), run_id=data.get("run_id"),
                         payload=data.get("payload") or {}, v=version)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"malformed {data.get('kind', 'message')}: {exc}") from exc
    raise ProtocolError(f"unknown kind: {kind!r}")
