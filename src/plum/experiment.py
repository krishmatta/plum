from __future__ import annotations

import abc
import itertools
from typing import Any, Callable, ClassVar, Iterator

from plum.catalog import Store
from plum.pipeline import RunManifest
from plum.registry import Registry


class Runner:
    """Runs a project's pipelines by name against one store.

    A plain executor -- no dependency resolution. Experiments express reuse and
    ordering directly: run a shared upstream once, pass its run id downstream.
    """

    def __init__(self, pipelines: Registry, store: Store) -> None:
        self._pipelines = pipelines
        self._store = store

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
        pipeline_cls = self._pipelines.get(pipeline)
        return pipeline_cls(self._store).run(
            run_id, force=force, resume=resume, description=description, **params
        )


class Experiment(abc.ABC):
    """A committed, named, runnable definition -- a reproducible script.

    `run` orchestrates pipelines through the `Runner`. Discovered like sources
    (`autodiscover`) and keyed by `id`.
    """

    id: ClassVar[str]

    @abc.abstractmethod
    def run(self, runner: Runner) -> None:
        raise NotImplementedError


def sweep(
    grid: dict[str, list], run_id: Callable[[dict], str]
) -> Iterator[tuple[dict, str]]:
    """Cartesian product over `grid`, each combo paired with a run id derived
    from its params. Deterministic: keys and values iterate in insertion order."""
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        yield params, run_id(params)
