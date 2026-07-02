from __future__ import annotations

from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic

M = TypeVar("M", bound=BaseModel)


class JsonModelCodec(Generic[M]):
    """Codec[M]: one model instance per .json file."""

    extension = ".json"

    def __init__(self, model: type[M]) -> None:
        self.model = model

    def write(self, obj: M, path: Path) -> None:
        write_atomic(path, lambda p: p.write_text(obj.model_dump_json(indent=2)))

    def read(self, path: Path) -> M:
        return self.model.model_validate_json(Path(path).read_text())


class JsonlCodec(Generic[M]):
    """Codec[list[M]]: one model instance per line of a .jsonl file."""

    extension = ".jsonl"

    def __init__(self, model: type[M]) -> None:
        self.model = model

    def write(self, objs: list[M], path: Path) -> None:
        def _write(p: Path) -> None:
            with p.open("w") as f:
                for obj in objs:
                    f.write(obj.model_dump_json())
                    f.write("\n")

        write_atomic(path, _write)

    def read(self, path: Path) -> list[M]:
        with Path(path).open() as f:
            return [self.model.model_validate_json(line) for line in f if line.strip()]
