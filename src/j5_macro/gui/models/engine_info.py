"""What the engine reported about itself in ``ready`` and ``hello``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EngineInfo:
    version: int | None
    """The negotiated protocol version."""
    backend: str | None
    """The input backend's name; None without one (dry runs still work)."""
    input_strategies: tuple[str, ...] = ()
    step_types: tuple[str, ...] = ()
    vision: bool | None = None
    stop_key: str | None = None
    """The emergency stop key the engine watches (``hello``'s ``stop_key``)."""
    display_scales: tuple[Mapping[str, Any], ...] = ()
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    """The raw ``hello`` capabilities, for fields this class does not know yet."""

    @classmethod
    def from_hello(cls, version: int | None, capabilities: Mapping[str, Any]) -> EngineInfo:
        backend = capabilities.get("backend")
        stop_key = capabilities.get("stop_key")
        vision = capabilities.get("vision")
        scales = capabilities.get("display_scales")
        return cls(
            version=version,
            backend=backend if isinstance(backend, str) else None,
            input_strategies=_strings(capabilities.get("input_strategies")),
            step_types=_strings(capabilities.get("step_types")),
            vision=vision if isinstance(vision, bool) else None,
            stop_key=stop_key if isinstance(stop_key, str) else None,
            display_scales=tuple(s for s in scales if isinstance(s, Mapping))
            if isinstance(scales, list) else (),
            capabilities=dict(capabilities),
        )

    def summary(self) -> str:
        """One line for the status bar."""
        parts = [f"backend: {self.backend}" if self.backend else "no input backend"]
        if self.vision is not None:
            parts.append("vision" if self.vision else "no vision")
        parts.append(f"stop key: {self.stop_key}" if self.stop_key else "no stop key")
        return " · ".join(parts)


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return ()
    return tuple(str(item) for item in raw)
