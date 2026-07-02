from __future__ import annotations

import abc
import importlib
import pkgutil
from types import ModuleType
from typing import ClassVar


class Source(abc.ABC):
    """Marker base for a pluggable strategy registered in a source family
    (datasets, confidence methods, …). The only contract is a registry key."""

    id: ClassVar[str]
    display_name: ClassVar[str] = ""


def autodiscover(package: ModuleType) -> None:
    """Import every submodule of `package` so registration decorators run,
    replacing hand-maintained `sources/__init__.py` import lists."""
    for _, name, _ in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(name)
