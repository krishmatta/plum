# plum

A small, opinionated library for research pipelines. Each stage produces one
versioned artifact; every run is reproducible by construction; orchestration is
plain, committed Python. It steals Kedro's data-catalog idea, keeps a resumable
execution core, and leans on **git + uv** so that the moment you produce an
artifact you can always answer *what code, what inputs, and what params made
this — and reproduce it.*

## Why plum

Most of what makes plum useful is invisible until you've been burned by its
absence. The design is a set of deliberate, opinionated defaults:

### Reproducibility by construction
**A run refuses to start if your git tree is dirty.** That one rule is the whole
game: because a dirty run is impossible, every run's `manifest.json` records the
exact commit that produced it. Six months later you don't guess which version of
the code made an artifact — you read its manifest and `git checkout <sha>`. No
archaeology, no "I think it was around then."

### The commit is a *complete* environment fingerprint (git + uv)
plum doesn't reinvent packaging — it defers entirely to **uv**. `uv init
--package` lays down the src-layout, `pyproject.toml`, a pinned
`.python-version`, and the git repo; `plum init` only drops in the plum-specific
files. The payoff compounds with the clean-tree rule: an uncommitted `uv.lock`
*is* a dirty tree, so a run can only happen when the lockfile is committed —
which means the recorded commit SHA pins not just your code but **every
dependency version and the Python itself**. That's why the manifest stores only
the commit, not a soup of package versions: the SHA already is them.

(uv is required to *scaffold* a project, not to run one — the library imports
fine under plain pip. `plum init` also gitignores `data/` so your own outputs
never dirty the tree.)

### One pipeline, one artifact
Every pipeline produces exactly one artifact into
`data/<artifact>/<scope>/<run_id>/`. The run directory is the unit of
everything — manifest, output, and checkpoints all live together — and
addressing stays trivial. No multi-output bookkeeping; a "variant" is just
another artifact with its own pipeline.

### Provenance you can trust
Every `ctx.read` is recorded in the manifest as an input edge — the upstream
artifact, run id, and scope — **plus a stamp of the exact generation read** (the
upstream's commit and finish time). So lineage is traceable backward, and
because run ids are mutable pointers, the stamp lets you *detect* when an
upstream was regenerated out from under a downstream instead of silently
believing a stale reference.

### Resumable by default
Expensive, interruptible work checkpoints into shards. Kill a six-hour job at
hour five, rerun, and it resumes at the last shard — skipping the expensive
setup entirely when there's nothing left to do. Finished runs are cached no-ops;
interrupted runs resume in place.

### Declare each artifact once
The catalog maps an artifact name to a codec (`JsonModelCodec`, `JsonlCodec`,
`ParquetCodec`, `TorchListCodec`, or your own) in exactly one place. Read and
write by name anywhere; swap the on-disk format without touching a single call
site. Registries + `autodiscover` mean you add a pipeline, method, or experiment
by dropping in a file — no import lists to maintain.

### Experiments are committed scripts, not config
Your ablation is Python, version-controlled, and git-clean-gated — so running it
pins the whole experiment to a commit. Reuse is trivial: run a shared upstream
once, pass its run id downstream. `sweep()` turns a param grid into runs with
deterministic, meaningful run ids. It replaces the ad-hoc `./scripts/` bash
every project grows.

### Runs carry intent
At a terminal, `run` opens `$EDITOR` for a description and aborts on an empty
message — git-commit UX — so a run records *why*, not just its params. Scripts
pass the message programmatically.

## The model

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
| `Experiment` | A committed, runnable script. Orchestrates pipelines via a `Runner`; `sweep()` fans out params. |

## Start a new project

plum defers project creation to uv, then scaffolds its own files:

```console
$ uv init --package myproj
$ cd myproj
$ uv add "plum @ git+https://github.com/krishmatta/plum"
$ uv run plum init
$ git add -A && git commit -m "scaffold"     # runs require a clean tree
$ uv run myproj experiments run demo
```

`plum init` renders `catalog.py`, `registries.py`, `schema.py`, a `pipelines/`
package, an `experiments/` package, and `app.py` into `src/myproj/`, and points
the console script at the app.

## A worked example

Lives at `tests/example/`, run by the test suite.

```python
# schema.py
class Numbers(BaseModel):
    values: list[int]

class Power(BaseModel):
    x: int
    y: int
```

```python
# catalog.py — every on-disk output, declared once
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
# pipelines/apply.py — reads a `load` run by id, checkpoints every 4 items
@PIPELINES.register
class Apply(Pipeline):
    name = "apply"
    produces = "powers"

    class Params(Pipeline.Params):
        numbers_run: str
        method: str = "square"

    def scope(self, params):
        return params.method  # -> data/powers/<method>/<run_id>/

    def _run(self, ctx):
        xs = ctx.read("numbers", ctx.params.numbers_run).values   # recorded as lineage
        shards = ctx.shards(len(xs), shard_size=4, codec=JsonlCodec(Power))
        if shards.pending:                       # empty on a resumed finished run
            method = METHODS.get(ctx.params.method)()
            for idx, sl in shards.pending:
                shards.write(idx, [Power(x=x, y=method.apply(x)) for x in xs[sl]])
        shards.finalize(ctx.output_path())
        ctx.stats["count"] = len(xs)
```

```python
# experiments/method_sweep.py — a reproducible sweep over a shared upstream
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
                sources={"methods": METHODS}, experiments=EXPERIMENTS)
```

## CLI

`build_cli` mounts a subcommand only for the capabilities a project declares.
Params are `key=value`, JSON-parsed when possible:

```console
$ myproj run load nums n=6 -m "baseline"     # -m, or $EDITOR opens at a terminal
load run 'nums': ok
data/numbers/nums/numbers.json

$ myproj run apply p1 numbers_run=nums method=cube -m "cube ablation"
$ myproj run load nums                         # same run id: cached, no prompt

$ myproj runs apply cube                       # table: id, status, started, finished, duration
$ myproj show apply p1 cube                    # dumps the manifest as JSON (pipe to jq)

$ myproj pipelines list                        # apply, load
$ myproj pipelines params apply
$ myproj methods list                          # a `list` per source family
$ myproj experiments list                      # method-sweep
$ myproj experiments run method-sweep
```

## Reruns

`run(run_id)` is idempotent, keyed on the manifest:

- `ok`: skipped.
- `running` (interrupted): resumes in place; a sharded pipeline recomputes only
  the missing shards and skips the expensive setup when none are.
- `error`: refuses with `PriorRunFailed`. `--resume` continues from checkpoints,
  `--force` starts over. A failing body always leaves a manifest with the full
  traceback.

Params are part of identity: rerunning or resuming a run id with different params
raises `ParamsMismatch` rather than silently reusing prior work. Every write is
atomic, so a crashed run never leaves a half-written artifact.

## Install

Core depends on `pydantic`, `typer`, and `click`. The parquet and torch codecs
import lazily behind extras:

```console
$ pip install plum[parquet]   # pyarrow
$ pip install plum[torch]     # torch
```
