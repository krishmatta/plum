from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

V = TypeVar("V")


@runtime_checkable
class Codec(Protocol[V]):
    """How one in-memory value becomes one file, and back. V is the file's whole
    value: Codec[Point] is a single-model file, Codec[list[Record]] a dataset."""

    extension: str

    def write(self, obj: V, path: Path) -> None: ...

    def read(self, path: Path) -> V: ...
