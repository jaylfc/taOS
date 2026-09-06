"""TinyAgentOS resource scheduler.

Central arbiter for inference work across NPU, GPU, CPU, and cluster workers.

Callers submit Tasks with a capability + priority + preferred-resource list;
the scheduler picks the first admitted resource and runs it. Availability is
driven by live backend probes (BackendCatalog), not filesystem state.

See docs/design/resource-scheduler.md for the full design.

Heavy submodules (history_store needs aiosqlite, task_scheduler / scheduler
pull in the full controller surface) are loaded lazily via PEP 562 module
``__getattr__``. The lightweight ``types`` module stays eager because it has
no third-party dependencies and is needed by every caller including the
worker. Workers can therefore ``from tinyagentos.scheduler.backend_catalog
import BACKEND_CAPABILITIES`` without dragging the controller's database
layer into the worker venv.
"""
from tinyagentos.scheduler.types import (
    Capability,
    NoResourceAvailableError,
    Priority,
    ResourceRef,
    ResourceSignature,
    Task,
    TaskRecord,
    TaskStatus,
)

# Map exported names → submodule that provides them. Resolved on first
# access via __getattr__ below and cached in module globals.
_LAZY_EXPORTS = {
    "BackendCatalog": "backend_catalog",
    "BackendEntry": "backend_catalog",
    "GpuArbiter": "gpu_arbiter",
    "HistoryStore": "history_store",
    "Resource": "resource",
    "Scheduler": "scheduler",
    "ScoreCache": "score_cache",
    "TaskScheduler": "task_scheduler",
}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        from importlib import import_module
        module = import_module(f"tinyagentos.scheduler.{_LAZY_EXPORTS[name]}")
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module 'tinyagentos.scheduler' has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY_EXPORTS))


__all__ = [
    "BackendCatalog",
    "BackendEntry",
    "Capability",
    "GpuArbiter",
    "HistoryStore",
    "NoResourceAvailableError",
    "Priority",
    "Resource",
    "ResourceRef",
    "ResourceSignature",
    "Scheduler",
    "ScoreCache",
    "Task",
    "TaskRecord",
    "TaskScheduler",
    "TaskStatus",
]
