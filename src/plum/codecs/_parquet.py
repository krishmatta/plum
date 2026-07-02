from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic

M = TypeVar("M", bound=BaseModel)


def _is_optional(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    return origin in (typing.Union, types.UnionType) and type(None) in typing.get_args(
        annotation
    )


def _arrow_type(annotation: Any):
    import pyarrow as pa

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _arrow_type(non_none[0])
        raise TypeError(f"cannot infer arrow type for union {annotation!r}; pass arrow_schema")

    if origin is list:
        if len(args) != 1:
            raise TypeError(f"cannot infer arrow type for {annotation!r}; pass arrow_schema")
        return pa.list_(_arrow_type(args[0]))

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return pa.list_(_arrow_type(args[0]))
        raise TypeError(
            f"cannot infer arrow type for {annotation!r}; "
            f"only homogeneous tuple[X, ...] is supported — pass arrow_schema"
        )

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return pa.struct(
            [_arrow_field(n, f.annotation) for n, f in annotation.model_fields.items()]
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


def _arrow_field(name: str, annotation: Any):
    import pyarrow as pa

    return pa.field(name, _arrow_type(annotation), nullable=_is_optional(annotation))


def infer_arrow_schema(model: type[BaseModel]):
    import pyarrow as pa

    return pa.schema(
        [_arrow_field(name, f.annotation) for name, f in model.model_fields.items()]
    )


@dataclass(frozen=True)
class RowConverter(Generic[M]):
    """The three fields must agree: to_row/from_row invert each other, and
    arrow_schema describes the rows to_row produces — hence one object, not
    separate kwargs."""

    arrow_schema: Any
    to_row: Callable[[M], dict]
    from_row: Callable[[dict], M]


class ParquetCodec(Generic[M]):
    extension = ".parquet"

    def __init__(self, model: type[M], *, converter: RowConverter[M] | None = None) -> None:
        self.model = model
        self._converter = converter

    @property
    def converter(self) -> RowConverter[M]:
        if self._converter is None:
            self._converter = RowConverter(
                arrow_schema=infer_arrow_schema(self.model),
                to_row=lambda m: m.model_dump(),
                from_row=self.model.model_validate,
            )
        return self._converter

    def write(self, objs: list[M], path: Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        conv = self.converter
        table = pa.Table.from_pylist([conv.to_row(o) for o in objs], schema=conv.arrow_schema)
        write_atomic(path, lambda p: pq.write_table(table, p))

    def read(self, path: Path) -> list[M]:
        import pyarrow.parquet as pq

        return [self.converter.from_row(row) for row in pq.read_table(path).to_pylist()]
