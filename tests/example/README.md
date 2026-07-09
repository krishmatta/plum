# Example

A complete plum project in miniature.

Two pipelines, two artifacts:

- `load` writes `numbers`: the integers `0..n`, one `numbers.json` per run.
- `apply` reads a `load` run by id, applies a `Method` source (`square` or
  `cube`) to each number, and writes `powers.jsonl`. Runs are scoped by
  method on disk (`powers/<method>/<run_id>/`). The body checkpoints every
  4 items, so an interrupted run resumes at the last complete shard.

One experiment:

- `method-sweep` runs `load` once, then sweeps `apply` over both methods
  through the `Runner`, so the shared upstream is computed exactly once.

One sync backend:

- `folder` (`backends/folder.py`) is a `SyncBackend` over a plain directory,
  the template for a custom `push`/`pull` remote. `autodiscover` registers it
  like a method.

The layout mirrors a real plum project:

```
schema.py       records (plain pydantic models)
catalog.py      artifact declarations
registries.py   the pipeline, method, and experiment registries
methods/        a source family; autodiscover imports it
pipelines/      the two stages
experiments/    the sweep
backends/       a custom sync backend
app.py          wiring: build_cli(...)
```

Drive it from the repo root:

```console
$ uv run python -m tests.example.app run load nums n=6 -m "baseline"
$ uv run python -m tests.example.app run apply p1 numbers_run=nums method=cube -m "cube"
$ uv run python -m tests.example.app methods list
$ uv run python -m tests.example.app experiments list
$ uv run python -m tests.example.app experiments run method-sweep
```

`tests/test_cli.py`, `tests/test_sources.py`, `tests/test_experiment.py`, and
`tests/test_descriptions.py` exercise it.
