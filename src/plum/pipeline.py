from __future__ import annotations

import abc
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, final

from pydantic import BaseModel, Field

from plum.catalog import Store
from plum.checkpoint import Shards
from plum.codecs import Codec, write_atomic
from plum.errors import ParamsMismatch, PriorRunFailed

import plum.config

MANIFEST_FILE = "manifest.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunManifest(BaseModel):
    """Reproducibility record written to every run directory as manifest.json.

    `stats` is free-form: each pipeline reports what it produced.
    """

    pipeline: str
    run_id: str
    status: str = "running"  # running | ok | error
    params: dict = {}
    stats: dict = {}
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
        self._produces = produces
        self._scope = scope

    def path(self, filename: str) -> Path:
        return self.run_dir / filename

    def read(self, artifact: str, run_id: str, *, scope: str | None = None) -> Any:
        return self.store.read(artifact, run_id, scope=scope)

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
        self, run_id: str, *, force: bool = False, resume: bool = False, **params
    ) -> RunManifest:
        if not run_id:
            raise ValueError("run_id is required")
        p = self.Params(**params)
        # An undeclared `produces` must fail here, not hours later at ctx.output().
        self.store.catalog.get(self.produces)
        scope = self.scope(p)
        run_dir = self.store.run_dir(self.produces, run_id, scope=scope)

        if run_dir.exists():
            if force:
                shutil.rmtree(run_dir)
            else:
                manifest_path = run_dir / MANIFEST_FILE
                if manifest_path.exists():
                    existing = load_manifest(manifest_path)
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
            pipeline=self.name, run_id=run_id, params=p.model_dump(mode="json")
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

    @staticmethod
    def _write_manifest(run_dir: Path, manifest: RunManifest) -> None:
        write_atomic(
            run_dir / MANIFEST_FILE,
            lambda p: p.write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            ),
        )
