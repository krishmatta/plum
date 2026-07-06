# plum

plum is a small library for research pipelines. A pipeline produces one
versioned artifact. Each run records the git commit that produced it and refuses
to run on a dirty working tree, so any artifact traces back to exact code.
Running pipelines and sweeping parameters is plain Python. The catalog/codec
design is from Kedro; the resumable execution core and git-based provenance are
plum's.

## Concepts

A project is a set of pipelines. Each execution of a pipeline is a run:

```
data/<artifact>/<scope>/<run_id>/
├── manifest.json      # status, description, params, stats, git commit, inputs, timing
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
| `Experiment` | A committed script. Runs pipelines through a `Runner`; `sweep()` fans out params. |

## Install

plum needs `pydantic`, `typer`, and `click`. The parquet and torch codecs are
optional extras:

```console
$ pip install data-plum            # import as `plum`
$ pip install data-plum[parquet]   # pyarrow
$ pip install data-plum[torch]     # torch
```

## Start a new project

plum defers project creation to uv:

```console
$ uv init --package myproj
$ cd myproj
$ uv add data-plum
$ uv run plum init
$ git add -A && git commit -m scaffold
$ uv run myproj experiments run demo
```

`plum init` writes `catalog.py`, `registries.py`, `schema.py`, a `pipelines/`
package, an `experiments/` package, and `app.py` into `src/myproj/`, and points
the console script at `app`. Runs require a clean tree, so commit first.

## Example

The full version is at `tests/example/`.

```python
# schema.py
class Numbers(BaseModel):
    values: list[int]

class Power(BaseModel):
    x: int
    y: int
```

```python
# catalog.py — every output declared once
CATALOG = Catalog()
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))  # numbers.json
CATALOG.register(Artifact("powers", JsonlCodec(Power)))         # powers.jsonl
```

```python
# registries.py
PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")
METHODS: Registry[type[Method]] = Registry("method")
EXPERIMENTS: Registry[type[Experiment]] = Registry("experiment")
```

```python
# methods/base.py, methods/cube.py — a source family, one Method per file
class Method(Source):
    @abc.abstractmethod
    def apply(self, x: int) -> int: ...

@METHODS.register
class Cube(Method):
    id = "cube"
    def apply(self, x): return x ** 3
```

```python
# pipelines/apply.py — reads a `load` run by id, checkpoints every 4 items
@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str
        method: str = "square"

    def scope(self, params):
        return params.method  # data/powers/<method>/<run_id>/

    def _run(self, ctx):
        xs = ctx.read("numbers", ctx.params.numbers_run).values
        shards = ctx.shards(len(xs), shard_size=4, codec=JsonlCodec(Power))
        if shards.pending:
            method = METHODS.get(ctx.params.method)()
            for idx, sl in shards.pending:
                shards.write(idx, [Power(x=x, y=method.apply(x)) for x in xs[sl]])
        shards.finalize(ctx.output_path())
        ctx.stats["count"] = len(xs)
```

```python
# experiments/method_sweep.py — a sweep over a shared upstream
@EXPERIMENTS.register
class MethodSweep(Experiment):
    id = "method-sweep"

    def run(self, runner):
        runner.run("load", "base", n=6, description="shared numbers")   # computed once
        for params, run_id in sweep({"method": ["square", "cube"]},
                                    run_id=lambda p: f"pow-{p['method']}"):
            runner.run("apply", run_id, numbers_run="base", method=params["method"],
                       description=f"powers via {params['method']}")
```

```python
# app.py
autodiscover(pipelines); autodiscover(methods); autodiscover(experiments)
app = build_cli(catalog=CATALOG, pipelines=PIPELINES,
                listings={"methods": METHODS}, experiments=EXPERIMENTS)
```

## CLI

`build_cli` mounts a subcommand only for the capabilities a project declares.
Params are `key=value`, JSON-parsed when possible.

```console
$ myproj run load nums n=6 -m "baseline"          # -m, or $EDITOR opens at a terminal
$ myproj run apply p1 numbers_run=nums method=cube -m "cube ablation"
$ myproj run load nums                             # same run id: cached, no prompt

$ myproj runs apply cube                           # id, status, started, finished, duration
$ myproj show apply p1 cube                        # manifest as JSON

$ myproj pipelines list
$ myproj pipelines params apply
$ myproj methods list                              # one `list` per listing family
$ myproj experiments list
$ myproj experiments run method-sweep
```

## Runs

`run(run_id)` is idempotent, keyed on the manifest:

- `ok`: skipped.
- `running` (interrupted): resumes in place. A sharded pipeline recomputes only
  the missing shards and skips setup when none are missing.
- `error`: refuses with `PriorRunFailed`. Use `--resume` to continue from
  checkpoints or `--force` to start over. A failed run leaves a manifest with the
  traceback.

Params are part of run identity: rerunning or resuming a run id with different
params raises `ParamsMismatch`. Every write is atomic, so a crashed run leaves no
partial artifact.

## Design decisions

### Runs require a clean git tree
A run refuses to start if `git status --porcelain` is nonempty. The manifest
records the commit. To reproduce an artifact, read its manifest and check out
that commit. Outside a git repo, runs proceed and record no commit.

### The commit pins the environment
plum defers packaging to uv. An uncommitted `uv.lock` makes the tree dirty, so a
run only happens with the lock committed. The commit then pins the dependency and
Python versions, and the manifest records the commit rather than a version list.
uv is required to scaffold a project, not to run one. `plum init` gitignores
`data/`.

### One artifact per pipeline
A pipeline writes one artifact. Its run directory holds the manifest, output, and
checkpoints. Multi-output pipelines are not supported; use separate pipelines.

### Reads are recorded
`ctx.read` records the upstream artifact, run id, and scope in the manifest, plus
the upstream's commit and finish time. Run ids are reused when a run is
regenerated, so the finish time lets a reader detect a stale reference.

### Experiments are code
Experiments are committed Python, not config. An experiment runs pipelines by
name with explicit run ids. `sweep()` expands a param grid into runs with derived
ids. Reuse is explicit: run a shared upstream once and pass its id. There is no
automatic dependency resolution.

### Run descriptions
At a terminal, `run` opens `$EDITOR` for a description and aborts on an empty
message. Scripts pass `-m` or a `description` argument.
