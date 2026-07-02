from __future__ import annotations

from plum import JsonlCodec, Pipeline

from tests.example.registries import METHODS, PIPELINES
from tests.example.schema import Numbers, Power


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


@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str
        method: str = "square"

    def scope(self, params):
        return params.method  # extra path segment: data/powers/<method>/<run_id>/

    def _run(self, ctx):
        # upstream artifact by run id; scope None because load runs are unscoped
        xs = ctx.read("numbers", None, ctx.params.numbers_run).values
        # checkpoint every 4 items; a rerun recomputes only missing shards
        shards = ctx.shards(len(xs), shard_size=4, codec=JsonlCodec(Power))
        if shards.pending:  # empty on a resumed finished run, so setup is skipped
            # sources are looked up by name at run time; stand-in for expensive setup
            method = METHODS.get(ctx.params.method)()
            for idx, sl in shards.pending:
                shards.write(idx, [Power(x=x, y=method.apply(x)) for x in xs[sl]])
        # concatenate the shards into the powers artifact at its catalog path
        shards.finalize(ctx.output_path())
        ctx.stats["count"] = len(xs)
