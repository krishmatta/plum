from __future__ import annotations

import abc
import itertools
from typing import Any, Callable, ClassVar, Iterator

from pydantic import BaseModel

from plum.catalog import RESERVED_ARTIFACTS, Artifact, Store
from plum.codecs import JsonModelCodec
from plum.errors import PlumError
from plum.run import RunManifest, RunRef, Runnable
from plum.registry import Registry

EXPERIMENTS_ARTIFACT = "experiments"


class InvocationRecord(BaseModel):
    """The output file of an experiment invocation. Deliberately minimal: a run
    wants an output, and the record's substance lives in the manifest's inputs."""

    experiment: str
    params: dict


RESERVED_ARTIFACTS.append(Artifact(EXPERIMENTS_ARTIFACT, JsonModelCodec(InvocationRecord)))


class Runner:
    """Runs a project's pipelines by name against one store.

    A plain executor -- no dependency resolution. Experiments express reuse and
    ordering directly: run a shared upstream once, pass its run id downstream.

    Composition is `invoke`: an experiment invocation is itself a run, so it
    records what it ensured as its inputs and syncs like any other run.
    `run`/`invoke` append a `RunRef` for each run they ensure to the open
    invocation frame, if any.
    """

    def __init__(
        self, pipelines: Registry, store: Store, experiments: Registry | None = None
    ) -> None:
        self._pipelines = pipelines
        self._store = store
        self._experiments = experiments
        self._frames: list[list[RunRef]] = []

    def run(
        self,
        pipeline: str,
        run_id: str,
        *,
        description: str | None = None,
        force: bool = False,
        resume: bool = False,
        **params: Any,
    ) -> RunManifest:
        pipe = self._pipelines.get(pipeline)(self._store)
        manifest = pipe.run(
            run_id, force=force, resume=resume, description=description, **params
        )
        self._record(pipe.produces, manifest)
        return manifest

    def invoke(
        self,
        experiment_id: str,
        invocation_id: str,
        *,
        description: str | None = None,
        force: bool = False,
        resume: bool = False,
        **params: Any,
    ) -> RunManifest:
        if self._experiments is None:
            raise PlumError(
                "this Runner has no experiments registry; pass experiments= to invoke"
            )
        experiment = self._experiments.get(experiment_id)(self._store, self)
        # the experiment's own _execute manages the invocation frame; record its
        # ref into the parent frame if this invoke is nested inside another
        manifest = experiment.run(
            invocation_id, force=force, resume=resume, description=description, **params
        )
        self._record(EXPERIMENTS_ARTIFACT, manifest)
        return manifest

    def _record(self, artifact: str, manifest: RunManifest) -> None:
        if not self._frames:
            return
        ref = RunRef(
            artifact=artifact,
            run_id=manifest.run_id,
            scope=manifest.scope,
            uuid=manifest.uuid,
            produced_at=manifest.finished_at,
            git_sha=manifest.git.sha if manifest.git else None,
        )
        frame = self._frames[-1]
        if ref not in frame:  # dedup like RunContext.read
            frame.append(ref)


class Experiment(Runnable):
    """A committed, named, runnable definition -- a reproducible script.

    `_run` orchestrates pipelines through the `Runner`. Discovered like sources
    (`autodiscover`) and keyed by `id`. `runner.invoke` executes an experiment as
    a recorded invocation, one run under the reserved experiments artifact. Not a
    passive producer -- it causes runs and records what it ensured -- which is why
    user pipelines never get runner access.
    """

    id: ClassVar[str]

    @property
    def name(self) -> str:
        # so ParamsMismatch and manifest.name cite this experiment
        return self.id

    @property
    def _artifact(self) -> str:
        return EXPERIMENTS_ARTIFACT

    def __init__(self, store: Store, runner: Runner) -> None:
        super().__init__(store)
        self._runner = runner

    def scope(self, params: BaseModel) -> str:
        return self.id

    @abc.abstractmethod
    def _run(self, runner: Runner, params: Params) -> None:
        """Drive pipelines through `runner`; `params` is the validated Params."""
        raise NotImplementedError

    def _execute(self, run_id, run_dir, params, scope, inputs, stats) -> None:
        # the frame IS inputs, so runs the experiment ensures land in the manifest
        # even if the body raises (Runnable.run's finally writes them)
        self._runner._frames.append(inputs)
        try:
            self._run(self._runner, params)
        finally:
            self._runner._frames.pop()
        self.store.write(
            EXPERIMENTS_ARTIFACT,
            run_id,
            InvocationRecord(experiment=self.id, params=params.model_dump(mode="json")),
            scope=scope,
        )


def sweep(
    grid: dict[str, list], run_id: Callable[[dict], str]
) -> Iterator[tuple[dict, str]]:
    """Cartesian product over `grid`, each combo paired with a run id derived
    from its params. Deterministic: keys and values iterate in insertion order."""
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        yield params, run_id(params)
