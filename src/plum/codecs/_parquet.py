from __future__ import annotations

import types
import typing
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic

M = TypeVar("M", bound=BaseModel)


def _arrow_type(annotation: Any):
    import pyarrow as pa

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _arrow_type(non_none[0])
        raise TypeError(f"cannot infer arrow type for union {annotation!r}; pass arrow_schema")

    if origin in (list, tuple):
        if len(args) != 1:
            raise TypeError(f"cannot infer arrow type for {annotation!r}; pass arrow_schema")
        return pa.list_(_arrow_type(args[0]))

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return pa.struct(
            [pa.field(n, _arrow_type(f.annotation)) for n, f in annotation.model_fields.items()]
        )

    scalars = {
        str: pa.string(),
        bool: pa.bool_(),
        int: pa.int64(),
        float: pa.float64(),
        bytes: pa.binary(),
    }
    if annotation in scalars:
        return scalars[annotation]

    raise TypeError(f"cannot infer arrow type for {annotation!r}; pass an explicit arrow_schema")


def infer_arrow_schema(model: type[BaseModel]):
    import pyarrow as pa

    return pa.schema(
        [pa.field(name, _arrow_type(f.annotation)) for name, f in model.model_fields.items()]
    )


class ParquetCodec(Generic[M]):
    extension = ".parquet"

    def __init__(
        self,
        model: type[M],
        *,
        arrow_schema: Any = None,
        to_row: Callable[[M], dict] | None = None,
        from_row: Callable[[dict], M] | None = None,
    ) -> None:
        self.model = model
        self._arrow_schema = arrow_schema
        self._to_row = to_row or (lambda m: m.model_dump())
        self._from_row = from_row or (lambda row: model(**row))

    @property
    def arrow_schema(self):
        if self._arrow_schema is None:
            self._arrow_schema = infer_arrow_schema(self.model)
        return self._arrow_schema

    def write(self, objs: list[M], path: Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist([self._to_row(o) for o in objs], schema=self.arrow_schema)
        write_atomic(path, lambda p: pq.write_table(table, p))

    def read(self, path: Path) -> list[M]:
        import pyarrow.parquet as pq

        return [self._from_row(row) for row in pq.read_table(path).to_pylist()]
