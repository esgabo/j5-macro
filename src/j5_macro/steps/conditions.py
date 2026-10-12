"""Predefined conditions for ``if.when`` and ``loop.while`` (docs/contract.md, "Script
format and step registry": conditions).

A predefined condition is written as a one-key mapping, ``{image_visible: img/x.png}``;
the interpreter calls ``fn(ctx, argument)`` with the argument already interpolated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from j5_macro.steps.images import image_visible
from j5_macro.steps.spec import StepContext

PREDEFINED_CONDITIONS: Mapping[str, Callable[[StepContext, Any], bool]] = MappingProxyType({
    "image_visible": image_visible,
})
