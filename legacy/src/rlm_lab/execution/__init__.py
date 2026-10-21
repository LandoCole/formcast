from __future__ import annotations

from typing import Any

__all__ = ["LabRunner", "RunOptions"]


def __getattr__(name: str) -> Any:
    if name in {"LabRunner", "RunOptions"}:
        from .runners import LabRunner, RunOptions

        exports = {
            "LabRunner": LabRunner,
            "RunOptions": RunOptions,
        }
        return exports[name]
    raise AttributeError(name)
