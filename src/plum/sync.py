from __future__ import annotations

import abc
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel

from plum.codecs import write_atomic
from plum.errors import PlumError, SyncConflict, UnknownName
from plum.pipeline import MANIFEST_FILE, RunManifest, load_manifest
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
        pass

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


@BACKENDS.register
class S3Backend(SyncBackend):
    id = "s3"

    class Options(BaseModel):
        bucket: str
        prefix: str = ""

    _client_cache = None

    def _client(self):
        if self._client_cache is None:
            import boto3

            self._client_cache = boto3.client("s3")
        return self._client_cache

    def _base(self) -> str:
        return f"{self.options.prefix}/" if self.options.prefix else ""

    def _key(self, relpath: str) -> str:
        return self._base() + relpath

    def list_runs(self) -> list[str]:
        import posixpath

        base = self._base()
        runs = []
        for key in self._iter_keys(base):
            relpath = key[len(base):]
            if posixpath.basename(relpath) == MANIFEST_FILE:
                runs.append(posixpath.dirname(relpath))
        return runs

    def list_files(self, run: str) -> list[str]:
        base = self._base()
        return [key[len(base):] for key in self._iter_keys(self._key(run) + "/")]

    def read_bytes(self, relpath: str) -> bytes:
        obj = self._client().get_object(Bucket=self.options.bucket, Key=self._key(relpath))
        return obj["Body"].read()

    def upload(self, src: Path, relpath: str) -> None:
        # upload_file streams via multipart, so artifact size is unbounded
        self._client().upload_file(str(src), self.options.bucket, self._key(relpath))

    def download(self, relpath: str, dest: Path) -> None:
        self._client().download_file(self.options.bucket, self._key(relpath), str(dest))

    def delete_run(self, run: str) -> None:
        client = self._client()
        for key in self._iter_keys(self._key(run) + "/"):
            client.delete_object(Bucket=self.options.bucket, Key=key)

    def _iter_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.options.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]


@dataclass(frozen=True)
class SyncResult:
    transferred: list[str]
    skipped: list[str]
    forced: list[str]


def load_remote(name: str = "origin", *, config_path: Path | str = "plum.toml") -> SyncBackend:
    """Resolve a remote from plum.toml. Every key but `backend` is a field of the
    chosen backend's Options and is pydantic-validated."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise PlumError(
            f"no {config_path} in {Path.cwd()}; declare a [remotes.<name>] table "
            "with a backend id and its options"
        )
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    remotes = data.get("remotes", {})
    if name not in remotes:
        raise UnknownName("remote", name, list(remotes))
    section = dict(remotes[name])
    backend_id = section.pop("backend", None)
    if backend_id is None:
        raise PlumError(f"remote '{name}' has no 'backend' key")
    backend_cls = BACKENDS.get(backend_id)
    return backend_cls(backend_cls.Options(**section))


def _local_runs(data_root: Path) -> Iterator[tuple[str, Path]]:
    if not data_root.exists():
        return
    for manifest_path in data_root.rglob(MANIFEST_FILE):
        run_dir = manifest_path.parent
        yield run_dir.relative_to(data_root).as_posix(), run_dir


def _local_dir(data_root: Path, relpath: str) -> Path:
    return data_root.joinpath(*relpath.split("/"))


def _read_remote_manifest(backend: SyncBackend, relpath: str) -> RunManifest | None:
    try:
        return RunManifest.model_validate_json(
            backend.read_bytes(f"{relpath}/{MANIFEST_FILE}")
        )
    except Exception:
        return None


def _timestamps(m: RunManifest) -> tuple[str, str | None]:
    return (m.started_at, m.finished_at)


def _local_status(data_root: Path, relpath: str, remote: RunManifest) -> str:
    """One of: absent, match, conflict -- comparing a candidate to any local run."""
    path = _local_dir(data_root, relpath) / MANIFEST_FILE
    if not path.exists():
        return "absent"
    try:
        local = load_manifest(path)
    except Exception:
        return "conflict"
    return "match" if _timestamps(local) == _timestamps(remote) else "conflict"


def _upload_run(backend: SyncBackend, data_root: Path, run_dir: Path, relpath: str) -> None:
    manifest_rel = f"{relpath}/{MANIFEST_FILE}"
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(data_root).as_posix()
        if rel != manifest_rel:  # manifest goes last: its presence must imply completeness
            backend.upload(p, rel)
    backend.upload(run_dir / MANIFEST_FILE, manifest_rel)


def _download_run(backend: SyncBackend, data_root: Path, relpath: str) -> None:
    manifest_rel = f"{relpath}/{MANIFEST_FILE}"
    for rel in backend.list_files(relpath):
        if rel != manifest_rel:
            write_atomic(_local_dir(data_root, rel), lambda p, r=rel: backend.download(r, p))
    write_atomic(
        _local_dir(data_root, manifest_rel),
        lambda p: backend.download(manifest_rel, p),
    )


def push(data_root: Path | str, backend: SyncBackend, *, force: bool = False) -> SyncResult:
    data_root = Path(data_root)
    local: dict[str, tuple[Path, RunManifest]] = {}
    for relpath, run_dir in _local_runs(data_root):
        try:
            m = load_manifest(run_dir / MANIFEST_FILE)
        except Exception:
            continue
        if m.status == "ok":  # a crashed or resuming run must never leak to the remote
            local[relpath] = (run_dir, m)

    remote = set(backend.list_runs())
    missing, skipped, conflicts = [], [], []
    for relpath, (run_dir, m) in local.items():
        if relpath not in remote:
            missing.append(relpath)
            continue
        remote_m = _read_remote_manifest(backend, relpath)
        if remote_m is not None and _timestamps(remote_m) == _timestamps(m):
            skipped.append(relpath)
        else:
            conflicts.append(relpath)

    if conflicts and not force:
        raise SyncConflict("push", conflicts)

    forced = conflicts if force else []
    for relpath in missing + forced:
        run_dir, _ = local[relpath]
        if relpath in remote:  # drop the old generation so files never mix
            backend.delete_run(relpath)
        _upload_run(backend, data_root, run_dir, relpath)
    return SyncResult(missing, skipped, forced)


def pull(
    data_root: Path | str,
    backend: SyncBackend,
    *,
    artifact: str | None = None,
    run_id: str | None = None,
    scope: str | None = None,
    force: bool = False,
) -> SyncResult:
    data_root = Path(data_root)
    if artifact is None:
        candidates = _remote_ok_runs(backend)
    else:
        if run_id is None:
            raise ValueError("pulling an artifact requires a run_id")
        candidates = _closure(backend, data_root, artifact, run_id, scope)

    missing, skipped, conflicts = [], [], []
    for relpath in candidates:
        status = _local_status(data_root, relpath, candidates[relpath])
        if status == "absent":
            missing.append(relpath)
        elif status == "match":
            skipped.append(relpath)
        else:
            conflicts.append(relpath)

    if conflicts and not force:
        raise SyncConflict("pull", conflicts)

    forced = conflicts if force else []
    for relpath in missing + forced:
        dest = _local_dir(data_root, relpath)
        if dest.exists():
            shutil.rmtree(dest)
        _download_run(backend, data_root, relpath)
    return SyncResult(missing, skipped, forced)


def _remote_ok_runs(backend: SyncBackend) -> dict[str, RunManifest]:
    out = {}
    for relpath in backend.list_runs():
        m = _read_remote_manifest(backend, relpath)
        if m is not None and m.status == "ok":
            out[relpath] = m
    return out


def _run_relpath(artifact: str, run_id: str, scope: str | None) -> str:
    return "/".join(s for s in (artifact, scope, run_id) if s)


def _closure(
    backend: SyncBackend, data_root: Path, artifact: str, run_id: str, scope: str | None
) -> dict[str, RunManifest]:
    """The transitive input-closure of one run, minus members already satisfied
    locally. Satisfied means local-only (the remote need not have it) or the same
    generation on both sides; a divergent local copy stays a candidate so the
    all-or-nothing conflict rule applies to lineage pulls too. A member present
    on neither side is a hard error."""
    to_fetch: dict[str, RunManifest] = {}
    seen: set[str] = set()
    queue = [(artifact, run_id, scope)]
    while queue:
        a, r, s = queue.pop()
        relpath = _run_relpath(a, r, s)
        if relpath in seen:
            continue
        seen.add(relpath)
        local_manifest = _local_dir(data_root, relpath) / MANIFEST_FILE
        remote_m = _read_remote_manifest(backend, relpath)
        if remote_m is None:
            if local_manifest.exists():
                continue
            raise PlumError(f"run '{relpath}' is on neither the remote nor local disk")
        if local_manifest.exists():
            try:
                local_m = load_manifest(local_manifest)
            except Exception:
                local_m = None
            if local_m is not None and _timestamps(local_m) == _timestamps(remote_m):
                continue  # same generation: no fetch, and its inputs need no traversal
        to_fetch[relpath] = remote_m
        # traverse the remote's inputs: a forced pull materializes that generation
        for ref in remote_m.inputs:
            queue.append((ref.artifact, ref.run_id, ref.scope))
    return to_fetch
