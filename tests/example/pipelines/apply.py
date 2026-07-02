from __future__ import annotations

from plum import JsonlCodec, Pipeline

from tests.example.registries import METHODS, PIPELINES
from tests.example.schema import Power


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
        # upstream artifact by run id; unscoped because load runs are unscoped
        xs = ctx.read("numbers", ctx.params.numbers_run).values
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
