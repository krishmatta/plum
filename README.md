# plum

A reusable spine for research pipelines. You write your records, your pipeline
bodies, and your pluggable strategies; plum handles everything they have in
common — on-disk layout, serialization, idempotent runs, crash recovery, and a
CLI.

## The model

A project is a set of **pipelines** (stages), each producing one **artifact**
(a named on-disk output). Every execution of a pipeline is a **run**, laid out
predictably on disk and recorded by a manifest:

```
data/<artifact>/<scope>/<run_id>/
├── manifest.json      # params, stats, status, timing, error
├── <artifact file>    # the output, e.g. generations.parquet
└── shards/            # checkpoint files, if the pipeline shards
```

Re-running is always safe: completed runs are skipped, interrupted runs resume
where they left off, and every file write is atomic.

| Concept | What it is |
|---|---|
| `Codec` | How one in-memory value becomes one file and back. `Codec[M]` is a single-model file, `Codec[list[M]]` a dataset. Builtins: `JsonModelCodec`, `JsonlCodec`, `ParquetCodec`, `TorchListCodec`. |
| `Artifact` | A named output type: name + codec (+ optional filename override). |
| `Catalog` | The registry of a project's artifact declarations. Declared once at import time. |
| `Store` | A catalog bound to a `data_root`. Resolves paths and does the IO; you mostly reach it through the run context. |
| `Pipeline` | One stage. Declares `name` and `produces` (its one artifact), validates a `Params` model, implements `_run(ctx)`. |
| `RunManifest` | `manifest.json` in every run dir — the record of what ran, with what params, and how it ended. |
| `Registry` | Generic name → object lookup. Backs pipelines and every source family; unknown names error with the known names listed. |
| `Source` | A pluggable strategy (a dataset, a scoring method, …) registered in a `Registry`. `autodiscover(pkg)` imports a package so registrations run — no hand-maintained import lists. |
| `Shards` | Resumable, checkpointed output within a single run. |

## Quickstart

```python
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
        ctx.output(Numbers(values=list(range(ctx.params.n))))

@PIPELINES.register
class Square(Pipeline):
    name = "square"
    produces = "squares"

    class Params(Pipeline.Params):
        numbers_run: str

    def _run(self, ctx):
        numbers = ctx.read("numbers", None, ctx.params.numbers_run)
        ctx.output(Numbers(values=[v * v for v in numbers.values]))

app = build_cli(catalog=CATALOG, pipelines=PIPELINES)
```

```console
$ myproj run load r1 n=100          # data/numbers/r1/numbers.json
$ myproj run square s1 numbers_run=r1
$ myproj pipelines list             # load, square
$ myproj pipelines params square    # numbers_run: str (required)
$ myproj runs load                  # r1
```

Params are `key=value` with JSON values where they parse (`n=3`, `flag=true`,
`tags=["a"]`); pipelines reject unknown params.

## Run lifecycle

`pipe.run(run_id, ...)` (or `run <pipeline> <run_id>` on the CLI):

- **Completed** (`status: ok`) → skipped; rerunning is a free no-op.
- **Interrupted** (`status: running`, e.g. ctrl-C) → resumes in place.
- **Failed** (`status: error`) → refuses with `PriorRunFailed`, quoting the
  stored exception. Pass `resume=True` (`--resume`) to continue from
  checkpoints, or `force=True` (`--force`) to wipe the run and start over.
- The body raising → the exception propagates, and the manifest records
  `status: error` with the full traceback, so failed runs stay inspectable.

Inside `_run(ctx)`: `ctx.params` (validated), `ctx.read(artifact, scope,
run_id)` for upstream inputs, `ctx.output(obj)` for the produced artifact,
`ctx.scratch(name, obj, codec)` for uncataloged inspectable intermediates,
`ctx.stats[...]` for anything worth recording in the manifest.

Runs are grouped on disk by overriding `scope()` — e.g. return
`params.dataset_id` to get `data/<artifact>/<dataset>/<run_id>/`. Default is
flat.

## Sharding: resume expensive work mid-run

For long interruptible batches (LLM calls, GPU inference), checkpoint at shard
granularity so a crash at item 9,000 of 10,000 costs one shard, not nine
hours:

```python
def _run(self, ctx):
    items = ctx.read("conversations", None, ctx.params.upstream_run)
    shards = ctx.shards(len(items), shard_size=100, codec=OUTPUT_CODEC)
    if shards.pending:
        backend = expensive_setup()          # only paid when there is work
        for idx, sl in shards.pending:
            shards.write(idx, compute(backend, items[sl]))
    shards.finalize(ctx.output_path())
```

Each shard covers a contiguous index range and is written atomically, so
whatever a crashed run left behind is trustworthy. On resume, `pending` lists
only the missing shards — a fully-computed run that died during finalize skips
straight past the expensive setup. `finalize` concatenates the shards into the
real artifact at its catalog-resolved location. Any list codec works.

## Source families

A source family is a contract + a registry + a package of implementations:

```python
class ConfidenceBase(Source):
    def compute(self, generations): ...

CONFIDENCE: Registry[ConfidenceBase] = Registry("confidence")
autodiscover(myproj.confidence.sources)   # imports the package; decorators register

app = build_cli(catalog=CATALOG, pipelines=PIPELINES,
                sources={"confidence": CONFIDENCE})   # adds `myproj confidence list`
```

## Install

Core depends only on `pydantic` and `typer`. Parquet and torch codecs import
lazily behind extras:

```console
$ pip install plum[parquet]   # pyarrow
$ pip install plum[torch]     # torch
```
