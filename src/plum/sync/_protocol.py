from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from plum.errors import PlumError, StaleLineage, SyncConflict
from plum.pipeline import MANIFEST_FILE, RunManifest, load_manifest
from plum.sync._backend import SyncBackend


@dataclass(frozen=True)
class SyncResult:
    transferred: list[str]
    skipped: list[str]
    forced: list[str]


def _local_runs(data_root: Path) -> Iterator[tuple[str, Path]]:
    if not data_root.exists():
        return
    for manifest_path in data_root.rglob(MANIFEST_FILE):
        run_dir = manifest_path.parent
        relpath = run_dir.relative_to(data_root)
        # stranded pull staging under .tmp must never be discovered as a run
        if any(part.startswith(".") for part in relpath.parts):
            continue
        yield relpath.as_posix(), run_dir


def _local_dir(data_root: Path, relpath: str) -> Path:
    return data_root.joinpath(*relpath.split("/"))


def _read_remote_manifest(backend: SyncBackend, relpath: str) -> RunManifest | None:
    try:
        return RunManifest.model_validate_json(
            backend.read_bytes(f"{relpath}/{MANIFEST_FILE}")
        )
    except Exception:
        return None


def _local_status(data_root: Path, relpath: str, remote: RunManifest) -> str:
    """One of: absent, match, conflict -- comparing a candidate to any local run."""
    path = _local_dir(data_root, relpath) / MANIFEST_FILE
    if not path.exists():
        return "absent"
    try:
        local = load_manifest(path)
    except Exception:
        return "conflict"
    return "match" if local.uuid == remote.uuid else "conflict"


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
    """Stage the whole run, then rename into place: the run appears atomically,
    and the destructive replace of any existing local copy happens only after a
    complete download."""
    # staging lives inside data_root so the final os.replace is a same-filesystem rename
    staging = data_root / ".tmp" / uuid.uuid4().hex
    try:
        for rel in backend.list_files(relpath):
            staged_file = staging.joinpath(*rel.split("/"))
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            backend.download(rel, staged_file)
        dest = _local_dir(data_root, relpath)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging.joinpath(*relpath.split("/")), dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
        if remote_m is not None and remote_m.uuid == m.uuid:
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
    """The transitive input-closure of one run, each member pinned to the
    generation the consuming manifest's RunRef recorded. The root is unpinned
    (taken at the remote's current generation); its refs pin everything below.

    A pinned member is satisfied only by a local copy of that exact generation;
    a pin the remote no longer holds raises StaleLineage rather than pairing the
    downstream with data it wasn't computed from. Refs without a uuid
    (externally-placed upstreams, no manifest at read time) keep the unpinned
    rules: satisfied by any local copy, fetched at remote current otherwise.
    A member on neither side is a hard error."""
    to_fetch: dict[str, RunManifest] = {}
    pins: dict[str, str | None] = {}
    queue: list[str] = []

    def visit(a: str, r: str, s: str | None, pin: str | None) -> None:
        relpath = _run_relpath(a, r, s)
        if relpath in pins:
            prior = pins[relpath]
            if pin is not None and prior is not None and pin != prior:
                raise StaleLineage(relpath, conflicting=(prior, pin))
            return
        pins[relpath] = pin
        queue.append(relpath)

    visit(artifact, run_id, scope, None)
    while queue:
        relpath = queue.pop()
        pin = pins[relpath]
        local_path = _local_dir(data_root, relpath) / MANIFEST_FILE
        local_m = None
        if local_path.exists():
            try:
                local_m = load_manifest(local_path)
            except Exception:
                local_m = None
        if pin is not None and local_m is not None and local_m.uuid == pin:
            continue  # the consumed generation is already local: no fetch, no traversal
        remote_m = _read_remote_manifest(backend, relpath)
        if pin is None:
            if remote_m is None:
                if local_path.exists():
                    continue  # locally satisfied; remote presence not required
                raise PlumError(f"run '{relpath}' is on neither the remote nor local disk")
            if local_m is not None and local_m.uuid == remote_m.uuid:
                continue  # same generation: no fetch, and its inputs need no traversal
        else:
            if remote_m is None and local_m is None:
                raise PlumError(f"run '{relpath}' is on neither the remote nor local disk")
            if remote_m is None:
                raise StaleLineage(relpath, expected=pin, found=local_m.uuid)
            if remote_m.uuid != pin:
                raise StaleLineage(relpath, expected=pin, found=remote_m.uuid)
        # candidates with a pin carry remote_m.uuid == pin, so pull()'s local-vs-remote
        # comparison is equivalent to comparing against the pin
        to_fetch[relpath] = remote_m
        for ref in remote_m.inputs:
            visit(ref.artifact, ref.run_id, ref.scope, ref.uuid)
    return to_fetch
