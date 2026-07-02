import pyarrow as pa
import pytest
from pydantic import BaseModel

from plum import (
    JsonModelCodec,
    ParquetCodec,
    TorchListCodec,
    infer_arrow_schema,
    write_atomic,
)


class Point(BaseModel):
    x: int
    y: int


class Record(BaseModel):
    id: str
    score: float
    active: bool
    tags: list[str]
    point: Point
    note: str | None = None


def test_write_atomic_creates_parents_and_no_tmp(tmp_path):
    target = tmp_path / "nested" / "dir" / "file.txt"
    write_atomic(target, lambda p: p.write_text("hi"))
    assert target.read_text() == "hi"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_write_atomic_cleans_up_on_failure(tmp_path):
    target = tmp_path / "file.txt"

    def boom(p):
        p.write_text("partial")
        raise RuntimeError("writer failed")

    with pytest.raises(RuntimeError):
        write_atomic(target, boom)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_json_model_codec_roundtrip(tmp_path):
    codec = JsonModelCodec(Point)
    path = tmp_path / "p.json"
    obj = Point(x=1, y=2)
    codec.write(obj, path)
    assert codec.read(path) == obj


def test_infer_arrow_schema():
    schema = infer_arrow_schema(Record)
    assert schema.field("id").type == pa.string()
    assert schema.field("score").type == pa.float64()
    assert schema.field("active").type == pa.bool_()
    assert schema.field("tags").type == pa.list_(pa.string())
    assert pa.types.is_struct(schema.field("point").type)
    assert schema.field("note").type == pa.string()


def test_infer_arrow_schema_nullability():
    schema = infer_arrow_schema(Record)
    assert not schema.field("id").nullable
    assert schema.field("note").nullable


def test_infer_arrow_schema_variadic_tuple():
    class WithTuple(BaseModel):
        vals: tuple[int, ...]

    schema = infer_arrow_schema(WithTuple)
    assert schema.field("vals").type == pa.list_(pa.int64())


def test_infer_arrow_schema_fixed_tuple_raises():
    class Fixed(BaseModel):
        pair: tuple[int, str]

    with pytest.raises(TypeError):
        infer_arrow_schema(Fixed)


def test_infer_arrow_schema_multi_union_raises():
    class Bad(BaseModel):
        v: int | str

    with pytest.raises(TypeError):
        infer_arrow_schema(Bad)


def test_parquet_codec_roundtrip(tmp_path):
    codec = ParquetCodec(Record)
    path = tmp_path / "r.parquet"
    records = [
        Record(id="a", score=1.5, active=True, tags=["x"], point=Point(x=1, y=2)),
        Record(id="b", score=2.0, active=False, tags=[], point=Point(x=3, y=4), note="n"),
    ]
    codec.write(records, path)
    assert codec.read(path) == records


def test_parquet_codec_custom_row_hooks(tmp_path):
    codec = ParquetCodec(
        Point,
        arrow_schema=pa.schema([pa.field("x", pa.int64()), pa.field("y", pa.int64())]),
        to_row=lambda m: {"x": m.x, "y": m.y},
        from_row=lambda row: Point(**row),
    )
    path = tmp_path / "pts.parquet"
    pts = [Point(x=1, y=2)]
    codec.write(pts, path)
    assert codec.read(path) == pts


def test_torch_list_codec_roundtrip(tmp_path):
    codec = TorchListCodec(Point)
    path = tmp_path / "pts.pth"
    pts = [Point(x=1, y=2), Point(x=3, y=4)]
    codec.write(pts, path)
    assert codec.read(path) == pts
