from __future__ import annotations

from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic
from plum.codecs._parquet import ParquetCodec, infer_arrow_schema
from plum.codecs._torch import TorchListCodec

M = TypeVar("M", bound=BaseModel)


@runtime_checkable
class Codec(Protocol):
    extension: str

    def write(self, obj: Any, path: Path) -> None: ...

    def read(self, path: Path) -> Any: ...


class JsonModelCodec(Generic[M]):
    extension = ".json"

    def __init__(self, model: type[M]) -> None:
        self.model = model

    def write(self, obj: M, path: Path) -> None:
        write_atomic(path, lambda p: p.write_text(obj.model_dump_json(indent=2)))

    def read(self, path: Path) -> M:
        return self.model.model_validate_json(Path(path).read_text())


__all__ = [
    "Codec",
    "JsonModelCodec",
    "ParquetCodec",
    "TorchListCodec",
    "infer_arrow_schema",
    "write_atomic",
]
