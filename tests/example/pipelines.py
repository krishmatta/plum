from __future__ import annotations

from plum import JsonlCodec, Pipeline

from tests.example.registries import METHODS, PIPELINES
from tests.example.schema import Numbers, Power


@PIPELINES.register
class Load(Pipeline):
    name = "load"
    produces = "numbers"

    class Params(Pipeline.Params):
        n: int = 10

    def _run(self, ctx):
        values = list(range(ctx.params.n))
        ctx.stats["count"] = len(values)
        ctx.output(Numbers(values=values))


@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str
        method: str = "square"

    def scope(self, params):
        return params.method

    def _run(self, ctx):
        xs = ctx.read("numbers", None, ctx.params.numbers_run).values
        shards = ctx.shards(len(xs), shard_size=4, codec=JsonlCodec(Power))
        if shards.pending:
            method = METHODS.get(ctx.params.method)()  # stand-in for expensive setup
            for idx, sl in shards.pending:
                shards.write(idx, [Power(x=x, y=method.apply(x)) for x in xs[sl]])
        shards.finalize(ctx.output_path())
        ctx.stats["count"] = len(xs)
