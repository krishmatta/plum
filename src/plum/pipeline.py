from __future__ import annotations

import abc
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from plum.catalog import Store
from plum.checkpoint import Shards
from plum.codecs import Codec
from plum.run import RunRef, Runnable, run_manifest


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
        inputs: list[RunRef],
        stats: dict,
    ):
        self.params = params
        self.run_id = run_id
        self.run_dir = run_dir
        self.store = store
        # the manifest's own lists, so recorded reads and stats land in it
        # even when the body raises (Runnable.run's finally writes them)
        self.stats = stats
        self._inputs = inputs
        self._produces = produces
        self._scope = scope

    def path(self, filename: str) -> Path:
        return self.run_dir / filename

    def read(self, artifact: str, run_id: str, *, scope: str | None = None) -> Any:
        obj = self.store.read(artifact, run_id, scope=scope)
        # upstream may have no plum manifest (e.g. an externally-placed artifact)
        m = run_manifest(self.store, artifact, run_id, scope=scope)
        ref = RunRef(
            artifact=artifact,
            run_id=run_id,
            scope=scope,
            uuid=m.uuid if m else None,
            produced_at=m.finished_at if m else None,
            git_sha=m.git.sha if m and m.git else None,
        )
        if ref not in self._inputs:
            self._inputs.append(ref)
        return obj

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


class Pipeline(Runnable):
    """Base class for all pipelines.

    Subclass contract: set `name` and `produces`, define a `Params` model, and
    implement `_run(ctx)`; override `scope()` to group runs (default is flat).
    See `Runnable` for the run lifecycle (skip/resume/force semantics).
    """

    produces: ClassVar[str]

    @property
    def _artifact(self) -> str:
        return self.produces

    @abc.abstractmethod
    def _run(self, ctx: RunContext) -> None:
        raise NotImplementedError

    def _execute(self, run_id, run_dir, params, scope, inputs, stats) -> None:
        ctx = RunContext(
            params=params,
            run_id=run_id,
            run_dir=run_dir,
            store=self.store,
            produces=self.produces,
            scope=scope,
            inputs=inputs,
            stats=stats,
        )
        self._run(ctx)
