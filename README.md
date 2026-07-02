# plum

A data plumbing library.

## The model

A project is a set of pipelines. Each pipeline produces one artifact (a named
on-disk output). Each execution of a pipeline is a run:

```
data/<artifact>/<scope>/<run_id>/
├── manifest.json      # params, stats, status, timing, error
├── <artifact file>
└── shards/            # checkpoints, if the pipeline shards
```

| Concept | Meaning |
|---|---|
| `Codec` | One in-memory value to one file and back. `Codec[M]` is a single-model file, `Codec[list[M]]` a dataset. Builtins: `JsonModelCodec`, `JsonlCodec`, `ParquetCodec`, `TorchListCodec`. |
| `Artifact` | A named output: name + codec, optional filename override. |
| `Catalog` | The registry of a project's artifact declarations. |
| `Store` | A catalog bound to a `data_root`. Resolves paths and does the IO. |
| `Pipeline` | One stage. Declares `name` and `produces`, validates `Params`, implements `_run(ctx)`. |
| `Registry` | Name to object lookup. Unknown names error with the known names listed. |
| `Source` | A pluggable strategy in a registry. `autodiscover(pkg)` imports a package so registrations run. |
| `Shards` | Checkpointed, resumable output within a run. |

## Example

Lives at `tests/example/`, run by the test suite.

Records:

```python
# myproj/schema.py
class Numbers(BaseModel):
    values: list[int]

class Power(BaseModel):
    x: int
    y: int
```

Artifacts:

```python
# myproj/catalog.py
CATALOG = Catalog()
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))  # numbers.json
CATALOG.register(Artifact("powers", JsonlCodec(Power)))         # powers.jsonl
```

Registries:

```python
# myproj/registries.py
PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")
METHODS: Registry[type[Method]] = Registry("method")
```

A source family. Modules register themselves; no import lists:

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

Pipelines. `Apply` reads a `load` run by id and checkpoints every 4 items:

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
        ctx.output(Numbers(values=values))


@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str          # which load run to read
        method: str = "square"

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

Wiring:

```python
# myproj/app.py
import myproj.methods
import myproj.pipelines            # registers the pipelines
from plum import autodiscover, build_cli

autodiscover(myproj.methods)

app = build_cli(catalog=CATALOG, pipelines=PIPELINES, sources={"methods": METHODS})
```

## CLI

Point a console script at `app` (`myproj = "myproj.app:app"` under
`[project.scripts]`). Params are `key=value`, JSON-parsed when possible:

```console
$ myproj run load nums n=6
load run 'nums': ok
data/numbers/nums/numbers.json

$ myproj run apply p1 numbers_run=nums method=cube
apply run 'p1': ok
data/powers/cube/p1/powers.jsonl

$ myproj run load nums              # same run id: skipped
$ myproj runs apply cube            # p1
$ myproj pipelines list             # apply, load
$ myproj pipelines params apply
numbers_run: str (required)
method: str = 'square'

$ myproj methods list
cube	Cube (x³)
square
```

On disk:

```
data/
├── numbers/nums/
│   ├── manifest.json
│   └── numbers.json
└── powers/cube/p1/          # scoped by method
    ├── manifest.json
    ├── powers.jsonl
    └── shards/              # shard_00000.jsonl ...
```

## Reruns

`run(run_id)` is idempotent, keyed on the manifest:

- `ok`: skipped.
- `running` (interrupted): resumes in place. A sharded pipeline recomputes
  only the missing shards, and skips the expensive setup when none are.
- `error`: refuses with `PriorRunFailed`, quoting the stored exception. Pass
  `--resume` to continue from checkpoints or `--force` to start over. A
  failing body always leaves a manifest with the full traceback.

Every write is atomic, so whatever a crashed run left behind is complete.

## Install

Core depends on `pydantic` and `typer`. The parquet and torch codecs import
lazily behind extras:

```console
$ pip install plum[parquet]   # pyarrow
$ pip install plum[torch]     # torch
```
