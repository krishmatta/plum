from __future__ import annotations

import shutil
from pathlib import Path

from plum import BACKENDS, SyncBackend, write_atomic
from plum.run import MANIFEST_FILE


@BACKENDS.register
class FolderBackend(SyncBackend):
    """A remote that is just another directory. The documented template for a
    custom backend: implement the transport, let push/pull drive the protocol."""

    id = "folder"

    class Options(SyncBackend.Options):
        path: str

    @property
    def _root(self) -> Path:
        return Path(self.options.path)

    def list_runs(self) -> list[str]:
        if not self._root.exists():
            return []
        return [
            p.parent.relative_to(self._root).as_posix()
            for p in self._root.rglob(MANIFEST_FILE)
        ]

    def list_files(self, run: str) -> list[str]:
        run_dir = self._root.joinpath(*run.split("/"))
        return [
            p.relative_to(self._root).as_posix()
            for p in run_dir.rglob("*")
            if p.is_file()
        ]

    def read_bytes(self, relpath: str) -> bytes:
        return self._root.joinpath(*relpath.split("/")).read_bytes()

    def upload(self, src: Path, relpath: str) -> None:
        write_atomic(
            self._root.joinpath(*relpath.split("/")),
            lambda p: shutil.copyfile(src, p),
        )

    def download(self, relpath: str, dest: Path) -> None:
        shutil.copyfile(self._root.joinpath(*relpath.split("/")), dest)

    def delete_run(self, run: str) -> None:
        run_dir = self._root.joinpath(*run.split("/"))
        if run_dir.exists():
            shutil.rmtree(run_dir)
