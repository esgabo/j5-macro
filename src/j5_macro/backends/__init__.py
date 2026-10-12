"""Platform backends, chosen by session type (Windows, X11; Wayland later)."""

from j5_macro.backends.base import (
    MODIFIER_KEYS,
    NAMED_KEYS,
    Backend,
    Button,
    Capabilities,
    Rect,
    UnknownKey,
)

__all__ = [
    "MODIFIER_KEYS",
    "NAMED_KEYS",
    "Backend",
    "Button",
    "Capabilities",
    "Rect",
    "UnknownKey",
]
