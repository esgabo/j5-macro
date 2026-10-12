"""Screen capture and image matching (milestone M3).

Importing this package never imports OpenCV, numpy or mss (the ``vision`` extra):
:func:`create_toolkit` does, and so does accessing the implementation names below
(``locate``, ``locate_all``, ``MssCapturer``, ``TemplateCache``, ``read_template``,
``save_png``), which are loaded on first use.
"""

from __future__ import annotations

import importlib
from typing import Any

from j5_macro.vision.api import (
    DEFAULT_INTERVAL,
    DEFAULT_THRESHOLD,
    Capturer,
    Frame,
    ImageSaver,
    Locator,
    Match,
    StepVision,
    Template,
    TemplateLoader,
    ToolkitFactory,
    VisionToolkit,
)
from j5_macro.vision.toolkit import create_toolkit

_LAZY = {
    "locate": "j5_macro.vision.matching",
    "locate_all": "j5_macro.vision.matching",
    "MssCapturer": "j5_macro.vision.capture",
    "TemplateCache": "j5_macro.vision.templates",
    "read_template": "j5_macro.vision.templates",
    "save_png": "j5_macro.vision.drawing",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_THRESHOLD",
    "Capturer",
    "Frame",
    "ImageSaver",
    "Locator",
    "Match",
    "StepVision",
    "Template",
    "TemplateLoader",
    "ToolkitFactory",
    "VisionToolkit",
    "create_toolkit",
]
