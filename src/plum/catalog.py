from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plum.codecs import Codec
from plum.errors import UnknownArtifact, UnknownName
from plum.registry import Registry


@dataclass(frozen=True)
class Artifact:
    name: str
    codec: Codec
    filename: str | None = None

    @property
    def resolved_filename(self) -> str:
        return self.filename or f"{self.name}{self.codec.extension}"


class Catalog(Registry[Artifact]):
    def __init__(self) -> None:
        super().__init__("artifact", key="name")

    def get(self, name: str) -> Artifact:
        try:
            return super().get(name)
        except UnknownName as e:
            raise UnknownArtifact(name, e.known) from None


class Store:
    """A Catalog bound to a data_root: knows where on disk, does the IO."""

    def __init__(self, catalog: Catalog, data_root: Path | str) -> None:
        self.catalog = catalog
        self.data_root = Path(data_root)

    def run_dir(self, artifact: str, run_id: str, *, scope: str | None = None) -> Path:
        return self.data_root.joinpath(*[s for s in (artifact, scope, run_id) if s])

    def path(self, artifact: str, run_id: str, *, scope: str | None = None) -> Path:
        a = self.catalog.get(artifact)
        return self.run_dir(artifact, run_id, scope=scope) / a.resolved_filename

    def read(self, artifact: str, run_id: str, *, scope: str | None = None) -> Any:
        a = self.catalog.get(artifact)
        return a.codec.read(self.path(artifact, run_id, scope=scope))

    def write(
        self, artifact: str, run_id: str, obj: Any, *, scope: str | None = None
    ) -> Path:
        a = self.catalog.get(artifact)
        p = self.path(artifact, run_id, scope=scope)
        a.codec.write(obj, p)
        return p
