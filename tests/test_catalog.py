import pytest
from pydantic import BaseModel

from plum import Artifact, Catalog, JsonModelCodec, Store, UnknownArtifact


class Point(BaseModel):
    x: int
    y: int


def make_store(tmp_path, artifact):
    catalog = Catalog()
    catalog.register(artifact)
    return Store(catalog, tmp_path)


def test_path_with_scope(tmp_path):
    store = make_store(tmp_path, Artifact("point", JsonModelCodec(Point)))
    path = store.path("point", "r1", scope="grp")
    assert path == tmp_path / "point" / "grp" / "r1" / "point.json"


def test_path_scope_none(tmp_path):
    store = make_store(tmp_path, Artifact("point", JsonModelCodec(Point)))
    path = store.path("point", "r1")
    assert path == tmp_path / "point" / "r1" / "point.json"


def test_path_custom_filename(tmp_path):
    store = make_store(tmp_path, Artifact("point", JsonModelCodec(Point), filename="p.json"))
    path = store.path("point", "r1")
    assert path == tmp_path / "point" / "r1" / "p.json"


def test_write_read_roundtrip(tmp_path):
    store = make_store(tmp_path, Artifact("point", JsonModelCodec(Point)))
    obj = Point(x=1, y=2)
    path = store.write("point", "r1", obj, scope="grp")
    assert path.exists()
    assert store.read("point", "r1", scope="grp") == obj


def test_unknown_artifact_raises(tmp_path):
    catalog = Catalog()
    catalog.register(Artifact("a", JsonModelCodec(Point)))
    catalog.register(Artifact("b", JsonModelCodec(Point)))
    store = Store(catalog, tmp_path)
    with pytest.raises(UnknownArtifact) as exc:
        store.path("missing", "r1")
    assert exc.value.known == ["a", "b"]
