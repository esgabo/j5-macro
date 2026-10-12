"""Step registry and built-in step types (milestone M1).

``default_registry()`` holds every v1 step; ``script_json_schema(registry)`` (in
:mod:`j5_macro.steps.schema`) builds the editor schema from it.
"""

from j5_macro.steps.builtins import default_registry
from j5_macro.steps.registry import ENTRY_POINT_GROUP, ParamCheck, StepRegistry
from j5_macro.steps.spec import (
    Duration,
    Executor,
    ParamProblem,
    ParamSpec,
    StepContext,
    StepError,
    StepLookup,
    StepSpec,
    StepType,
    parse_duration,
    validate_params,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "Duration",
    "Executor",
    "ParamCheck",
    "ParamProblem",
    "ParamSpec",
    "StepContext",
    "StepError",
    "StepLookup",
    "StepRegistry",
    "StepSpec",
    "StepType",
    "default_registry",
    "parse_duration",
    "validate_params",
]
