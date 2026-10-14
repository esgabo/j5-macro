"""Step registry and built-in step types (milestone M1).

``default_registry()`` holds every v1 step, ``registry_with_plugins()`` adds the installed
step plugins; ``script_json_schema(registry)`` (in :mod:`j5_macro.steps.schema`) builds the
editor schema from a registry.
"""

from j5_macro.steps.builtins import default_registry, registry_with_plugins
from j5_macro.steps.registry import (
    ENTRY_POINT_GROUP,
    ParamCheck,
    PluginProblem,
    PluginReport,
    StepRegistry,
)
from j5_macro.steps.spec import (
    Duration,
    Executor,
    ParamProblem,
    ParamSpec,
    ResultField,
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
    "PluginProblem",
    "PluginReport",
    "ResultField",
    "StepContext",
    "StepError",
    "StepLookup",
    "StepRegistry",
    "StepSpec",
    "StepType",
    "default_registry",
    "parse_duration",
    "registry_with_plugins",
    "validate_params",
]
