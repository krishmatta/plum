from __future__ import annotations

from plum import Pipeline

from tests.example.registries import PIPELINES
from tests.example.schema import Numbers


@PIPELINES.register
class Load(Pipeline):
    name = "load"
    # the catalog artifact this pipeline writes; also its dir under data/
    produces = "numbers"

    class Params(Pipeline.Params):
        n: int = 10

    def _run(self, ctx):
        values = list(range(ctx.params.n))
        ctx.stats["count"] = len(values)  # recorded in this run's manifest.json
        # encoded by the artifact's codec into the run dir
        ctx.output(Numbers(values=values))
