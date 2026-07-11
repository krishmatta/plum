from __future__ import annotations

import abc
import shutil
import subprocess
import traceback
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, final

from pydantic import BaseModel, Field

from plum.catalog import Store
from plum.checkpoint import Shards
from plum.codecs import Codec, write_atomic
from plum.errors import DirtyWorkingTree, ParamsMismatch, PriorRunFailed

import plum.config

MANIFEST_FILE = "manifest.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitInfo(BaseModel):
    sha: str
    branch: str | None = None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


def capture_git(cwd: Path) -> GitInfo | None:
    """The commit a run maps to. Raise DirtyWorkingTree in a dirty repo; None
    outside one. With a clean tree and a committed uv.lock, the sha pins the
    exact code and dependencies, so nothing else needs recording."""
    if shutil.which("git") is None:
        return None
    if _git(cwd, "rev-parse", "--show-toplevel").returncode != 0:
        return None  # not inside a git repo
    repo = _git(cwd, "rev-parse", "--show-toplevel").stdout.strip()
    head = _git(cwd, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise DirtyWorkingTree(repo, "has no commits yet")
    if _git(cwd, "status", "--porcelain").stdout.strip():
        raise DirtyWorkingTree(repo, "has uncommitted changes")
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return GitInfo(sha=head.stdout.strip(), branch=branch or None)


class RunRef(BaseModel):
    """A stamped pointer to one run generation, used wherever a manifest
    references another run (a run's inputs today).

    `uuid` pins the exact generation that was read. Run ids are mutable -- a
    force-regenerated run reuses the id but rewrites its content -- so the
    reference alone can later point at a different generation than the one
    actually consumed. The pin makes that staleness detectable: it differs
    from the upstream's current manifest once the upstream is regenerated.
    `produced_at` and `git_sha` stay for humans and reproduction.
    """

    artifact: str
    run_id: str
    scope: str | None = None
    uuid: str | None = None
    produced_at: str | None = None
    git_sha: str | None = None


class RunManifest(BaseModel):
    """Reproducibility record written to every run directory as manifest.json.

    `stats` is free-form: each pipeline reports what it produced.
    """

    pipeline: str
    run_id: str
    uuid: str
    scope: str | None = None
    status: str = "running"  # running | ok | error
    description: str | None = None
    params: dict = {}
    stats: dict = {}
    git: GitInfo | None = None
    inputs: list[RunRef] = []
    started_at: str = Field(default_factory=_timestamp)
    finished_at: str | None = None
    error: str | None = None


def load_manifest(path: Path | str) -> RunManifest:
    return RunManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


class RunContext:
    """Handed to `_run`. Owns the run directory and its outputs.

    - `ctx.params` -> the pipeline's validated `Params` instance.
    - `ctx.path(name)` -> a path inside the run directory.
    - `ctx.read(artifact, run_id, scope=...)` -> an upstream artifact.
    - `ctx.output(obj)` / `ctx.output_path()` -> write / locate the `produces` artifact.
    - `ctx.scratch(name, obj, codec)` -> uncataloged inspectable intermediate.
    - `ctx.shards(...)` -> resumable sharded output, so expensive interruptible
      work restarts at the last shard instead of from scratch (see `Shards`).
    - `ctx.stats[...]` -> anything worth recording in the manifest.
    """

    def __init__(
        self,
        *,
        params: BaseModel,
        run_id: str,
        run_dir: Path,
        store: Store,
        produces: str,
        scope: str | None,
    ):
        self.params = params
        self.run_id = run_id
        self.run_dir = run_dir
        self.store = store
        self.stats: dict = {}
        self._inputs: list[RunRef] = []
        self._produces = produces
        self._scope = scope

    def path(self, filename: str) -> Path:
        return self.run_dir / filename

    def read(self, artifact: str, run_id: str, *, scope: str | None = None) -> Any:
        obj = self.store.read(artifact, run_id, scope=scope)
        ref = self._input_ref(artifact, run_id, scope)
        if ref not in self._inputs:
            self._inputs.append(ref)
        return obj

    def _input_ref(self, artifact: str, run_id: str, scope: str | None) -> RunRef:
        upstream_uuid = produced_at = git_sha = None
        try:
            m = load_manifest(
                self.store.run_dir(artifact, run_id, scope=scope) / MANIFEST_FILE
            )
            upstream_uuid = m.uuid
            produced_at = m.finished_at
            git_sha = m.git.sha if m.git else None
        except Exception:
            pass  # upstream has no plum manifest (e.g. an externally-placed artifact)
        return RunRef(
            artifact=artifact,
            run_id=run_id,
            scope=scope,
            uuid=upstream_uuid,
            produced_at=produced_at,
            git_sha=git_sha,
        )

    def output(self, obj: Any) -> Path:
        return self.store.write(self._produces, self.run_id, obj, scope=self._scope)

    def output_path(self) -> Path:
        return self.store.path(self._produces, self.run_id, scope=self._scope)

    def scratch(self, name: str, obj: Any, codec: Codec) -> Path:
        path = self.run_dir / f"{name}{codec.extension}"
        codec.write(obj, path)
        return path

    def shards(self, n_items: int, shard_size: int, codec: Codec) -> Shards:
        return Shards(self.run_dir, n_items, shard_size, codec)


class Pipeline(abc.ABC):
    """Base class for all pipelines.

    The base creates `<data_root>/<produces>/[<scope>/]<run_id>/`, runs the body,
    and writes manifest.json. Re-running an existing run id is cheap and safe:
    completed runs are skipped, interrupted runs resume in place, error runs
    require `resume=True` to continue, and `force=True` starts over from
    scratch.

    Subclass contract: set `name` and `produces`, define a `Params` model, and
    implement `_run(ctx)`; override `scope()` to group runs (default is flat).
    """

    name: ClassVar[str]
    produces: ClassVar[str]

    class Params(plum.config.Params):
        pass

    def __init__(self, store: Store):
        self.store = store

    def scope(self, params: BaseModel) -> str | None:
        """Override point: the path segment grouping runs, between artifact dir and run id."""
        return None

    @abc.abstractmethod
    def _run(self, ctx: RunContext) -> None:
        raise NotImplementedError

    @final
    def run(
        self,
        run_id: str,
        *,
        force: bool = False,
        resume: bool = False,
        description: str | None = None,
        **params,
    ) -> RunManifest:
        if not run_id:
            raise ValueError("run_id is required")
        p = self.Params(**params)
        # An undeclared `produces` must fail here, not hours later at ctx.output().
        self.store.catalog.get(self.produces)
        # Refuse a dirty tree before touching disk, so every run maps to a commit.
        git = capture_git(Path.cwd())
        scope = self.scope(p)
        run_dir = self.store.run_dir(self.produces, run_id, scope=scope)

        carried_description: str | None = None
        if run_dir.exists():
            if force:
                shutil.rmtree(run_dir)
            else:
                manifest_path = run_dir / MANIFEST_FILE
                if manifest_path.exists():
                    existing = load_manifest(manifest_path)
                    carried_description = existing.description
                    requested = p.model_dump(mode="json")
                    if existing.params != requested:
                        raise ParamsMismatch(
                            self.name, run_id, existing.params, requested
                        )
                    if existing.status == "ok":
                        return existing  # already done; rerunning is a no-op
                    if existing.status == "error" and not resume:
                        raise PriorRunFailed(self.name, run_id, existing.error)
                # otherwise: interrupted run, resume in place
        run_dir.mkdir(parents=True, exist_ok=True)

        manifest = RunManifest(
            pipeline=self.name,
            run_id=run_id,
            # so a RunRef can be stamped from this manifest alone
            scope=scope,
            # every execution attempt (including a resume) is a new generation
            uuid=uuidlib.uuid4().hex,
            description=description if description is not None else carried_description,
            params=p.model_dump(mode="json"),
            git=git,
        )
        self._write_manifest(run_dir, manifest)
        ctx = RunContext(
            params=p,
            run_id=run_id,
            run_dir=run_dir,
            store=self.store,
            produces=self.produces,
            scope=scope,
        )
        try:
            self._run(ctx)
            manifest.status = "ok"
        except Exception:
            manifest.status = "error"
            manifest.error = traceback.format_exc()
            raise
        finally:
            manifest.stats = ctx.stats
            manifest.inputs = ctx._inputs
            manifest.finished_at = _timestamp()
            self._write_manifest(run_dir, manifest)
        return manifest

    @final
    def list_runs(self, scope: str | None = None) -> list[str]:
        runs_dir = self.store.data_root.joinpath(
            *[s for s in (self.produces, scope) if s]
        )
        if not runs_dir.exists():
            return []
        return sorted(p.name for p in runs_dir.iterdir() if p.is_dir())

    @final
    def manifest(self, run_id: str, *, scope: str | None = None) -> RunManifest | None:
        path = self.store.run_dir(self.produces, run_id, scope=scope) / MANIFEST_FILE
        if not path.exists():
            return None
        try:
            return load_manifest(path)
        except Exception:
            return None

    @staticmethod
    def _write_manifest(run_dir: Path, manifest: RunManifest) -> None:
        write_atomic(
            run_dir / MANIFEST_FILE,
            lambda p: p.write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            ),
        )
