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
├── <artifact file>    # the output
└── shards/            # checkpoint files, if the pipeline shards
```

| Concept | What it is |
|---|---|
| `Codec` | How one in-memory value becomes one file and back. `Codec[M]` is a single-model file, `Codec[list[M]]` a dataset. Builtins: `JsonModelCodec`, `JsonlCodec`, `ParquetCodec`, `TorchListCodec`. |
| `Artifact` | A named output type: name + codec (+ optional filename override). |
| `Catalog` | The registry of a project's artifact declarations. Declared once at import time. |
| `Store` | A catalog bound to a `data_root`. Resolves paths and does the IO; you mostly reach it through the run context. |
| `Pipeline` | One stage. Declares `name` and `produces` (its one artifact), validates a `Params` model, implements `_run(ctx)`. |
| `Registry` | Generic name → object lookup. Backs pipelines and source families; unknown names error with the known names listed. |
| `Source` | A pluggable strategy looked up by name in a registry. `autodiscover(pkg)` imports a package so registrations run. |
| `Shards` | Resumable, checkpointed output within a single run. |

## The example

One self-contained project — six small files. It ships as `tests/example/`
and is exercised by the test suite, so it can't drift from reality.

**Records** are plain pydantic models:

```python
# myproj/schema.py
class Numbers(BaseModel):
    values: list[int]

class Power(BaseModel):
    x: int
    y: int
```

**The catalog** declares each on-disk output once — a name and a codec:

```python
# myproj/catalog.py
CATALOG = Catalog()
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))  # numbers.json
CATALOG.register(Artifact("powers", JsonlCodec(Power)))         # powers.jsonl
```

**Registries** hold the interchangeable pieces — the pipelines, and one
registry per source family:

```python
# myproj/registries.py
PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")
METHODS: Registry[type[Method]] = Registry("method")
```

**A source family** is a contract plus a package of implementations. Modules
register themselves; nobody maintains an import list (`autodiscover` in
`app.py` imports the package):

```python
# myproj/methods/base.py
class Method(Source):
    @abc.abstractmethod
    def apply(self, x: int) -> int: ...

# myproj/methods/cube.py        (square.py is the same idea)
@METHODS.register
class Cube(Method):
    id = "cube"
    display_name = "Cube (x³)"

    def apply(self, x: int) -> int:
        return x**3
```

**Pipelines**: `Load` produces the `numbers` artifact; `Apply` consumes a
`load` run and produces `powers`. Upstream wiring is explicit — you name the
run you want to read:

```python
# myproj/pipelines.py
@PIPELINES.register
class Load(Pipeline):
    name = "load"
    produces = "numbers"

    class Params(Pipeline.Params):
        n: int = 10

    def _run(self, ctx):
        values = list(range(ctx.params.n))
        ctx.stats["count"] = len(values)          # recorded in the manifest
        ctx.output(Numbers(values=values))        # writes numbers.json


@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str          # which load run to read
        method: str = "square"    # which Method source to use

    def scope(self, params):
        return params.method      # runs grouped on disk: powers/<method>/<run_id>/

    def _run(self, ctx):
        xs = ctx.read("numbers", None, ctx.params.numbers_run).values  # None: load is unscoped
        shards = ctx.shards(len(xs), shard_size=4, codec=JsonlCodec(Power))
        if shards.pending:
            method = METHODS.get(ctx.params.method)()  # stand-in for expensive setup
            for idx, sl in shards.pending:
                shards.write(idx, [Power(x=x, y=method.apply(x)) for x in xs[sl]])
        shards.finalize(ctx.output_path())
        ctx.stats["count"] = len(xs)
```

`Apply` is sharded: it checkpoints every 4 items (in real life: every N LLM
calls or GPU batches) so an interrupted run resumes instead of restarting.
More below.

**The app** is pure wiring:

```python
# myproj/app.py
import myproj.methods
import myproj.pipelines            # registers the pipelines
from plum import autodiscover, build_cli

autodiscover(myproj.methods)       # imports the package; sources register

app = build_cli(catalog=CATALOG, pipelines=PIPELINES, sources={"methods": METHODS})
```

## Driving it

Point a console script at `app` (`myproj = "myproj.app:app"` under
`[project.scripts]`). Params are `key=value`, JSON-parsed where possible:

```console
$ myproj run load nums n=6
load run 'nums': ok
data/numbers/nums/numbers.json

$ myproj run apply p1 numbers_run=nums method=cube
apply run 'p1': ok
data/powers/cube/p1/powers.jsonl

$ myproj run load nums              # same run id → already done; instant no-op
$ myproj runs apply cube            # p1
$ myproj pipelines list             # apply, load
$ myproj pipelines params apply
numbers_run: str (required)
method: str = 'square'

$ myproj methods list
cube	Cube (x³)
square
```

Leaving on disk:

```
data/
├── numbers/nums/
│   ├── manifest.json
│   └── numbers.json
└── powers/cube/p1/          # scoped by method
    ├── manifest.json
    ├── powers.jsonl
    └── shards/              # shard_00000.jsonl … the checkpoints
```

## Reruns, crashes, failures

`run(run_id)` is idempotent, keyed on the manifest:

- **Completed** (`status: ok`) → skipped, free.
- **Interrupted** (`status: running`, e.g. ctrl-C) → resumes in place. For a
  sharded pipeline that means `shards.pending` lists only the missing index
  ranges — finished shards are never recomputed, and the expensive setup
  behind `if shards.pending:` isn't even constructed when nothing is left.
- **Failed** (`status: error`) → refuses with `PriorRunFailed`, quoting the
  stored exception; pass `--resume` to continue from checkpoints or `--force`
  to wipe and start over. A failing body always leaves a manifest with the
  full traceback.

Every file plum writes — artifacts, shards, manifests — is written atomically,
so whatever a crashed run left behind is complete and trustworthy.

## Install

Core depends only on `pydantic` and `typer`. The parquet and torch codecs
import lazily behind extras:

```console
$ pip install plum[parquet]   # pyarrow
$ pip install plum[torch]     # torch
```
