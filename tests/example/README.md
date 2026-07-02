# Example

A complete plum project in miniature.

Two pipelines, two artifacts:

- `load` writes `numbers`: the integers `0..n`, one `numbers.json` per run.
- `apply` reads a `load` run by id, applies a `Method` source (`square` or
  `cube`) to each number, and writes `powers.jsonl`. Runs are scoped by
  method on disk (`powers/<method>/<run_id>/`). The body checkpoints every
  4 items, so an interrupted run resumes at the last complete shard.

The layout mirrors a real plum project:

```
schema.py       records (plain pydantic models)
catalog.py      artifact declarations
registries.py   the pipeline and source registries
methods/        a source family; autodiscover imports it
pipelines.py    the two stages
app.py          wiring: build_cli(...)
```

Drive it from the repo root:

```console
$ uv run python -m tests.example.app run load nums n=6
$ uv run python -m tests.example.app run apply p1 numbers_run=nums method=cube
$ uv run python -m tests.example.app methods list
```

`tests/test_cli.py` and `tests/test_sources.py` exercise it.
