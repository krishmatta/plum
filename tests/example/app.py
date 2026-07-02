from __future__ import annotations

from pydantic import BaseModel

from plum import Artifact, Catalog, JsonModelCodec, Pipeline, Registry, build_cli


class Numbers(BaseModel):
    values: list[int]


CATALOG = Catalog()
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))
CATALOG.register(Artifact("squares", JsonModelCodec(Numbers)))

PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")


@PIPELINES.register
class Load(Pipeline):
    name = "load"
    produces = "numbers"

    class Params(Pipeline.Params):
        n: int = 5

    def _run(self, ctx):
        values = list(range(ctx.params.n))
        ctx.stats["count"] = len(values)
        ctx.output(Numbers(values=values))


@PIPELINES.register
class Square(Pipeline):
    name = "square"
    produces = "squares"

    class Params(Pipeline.Params):
        numbers_run: str

    def _run(self, ctx):
        numbers = ctx.read("numbers", None, ctx.params.numbers_run)
        squares = [v * v for v in numbers.values]
        ctx.stats["count"] = len(squares)
        ctx.output(Numbers(values=squares))


app = build_cli(catalog=CATALOG, pipelines=PIPELINES)
