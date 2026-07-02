# plum

A reusable research-pipeline library — pipelines, artifacts, an on-disk data catalog, and
registries, extracted from the scaffolding that recurs across research projects.

The core depends only on `pydantic` and `typer`. Storage backends are optional extras:

- `plum[parquet]` — Parquet codecs (pyarrow)
- `plum[torch]` — tensor codecs (torch)

Status: early scaffolding (Phase 0).
