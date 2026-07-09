from __future__ import annotations

import abc
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from plum.registry import Registry
from plum.sources import Source


class SyncBackend(Source):
    """Transport for whole run directories between a local data_root and a remote.

    Relpaths are always relative to the data root: `list_files`, `read_bytes`,
    `upload`, and `download` speak full relpaths like "numbers/r1/manifest.json";
    `list_runs` and `delete_run` speak run-dir relpaths like "numbers/r1". The
    sync protocol -- completeness filtering, conflict checks, manifest-last
    ordering -- lives in `push`/`pull`; a backend only moves bytes.

    Files transfer as streams (`upload`/`download`), never as whole in-memory
    buffers -- artifacts can exceed memory and single-PUT limits. `read_bytes`
    exists only for manifests, which are tiny. `download` may assume `dest`'s
    parent directory already exists (core hands it a write_atomic temp path).
    """

    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")

    def __init__(self, options: "SyncBackend.Options") -> None:
        self.options = options

    @abc.abstractmethod
    def list_runs(self) -> list[str]:
        """Relpaths of every remote dir containing a manifest.json."""
        raise NotImplementedError

    @abc.abstractmethod
    def list_files(self, run: str) -> list[str]:
        """Full relpaths of every file under the given run dir."""
        raise NotImplementedError

    @abc.abstractmethod
    def read_bytes(self, relpath: str) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def upload(self, src: Path, relpath: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def download(self, relpath: str, dest: Path) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def delete_run(self, run: str) -> None:
        raise NotImplementedError


BACKENDS: Registry[type[SyncBackend]] = Registry("backend")
