# plum

A reusable research-pipeline library: pipelines, artifacts, catalog, registries.
Built in phases; currently at the primitives stage (registry, codecs, config).

## Working model

When running as Fable, act as an orchestrator only: deploy Opus subagents to
perform the actual execution (code edits, running commands), then review their
work and give feedback. Do not make code changes directly.

## Commands

- Test: `uv run pytest -q`

## Conventions

- **Comments/docstrings: high-signal only.** A comment must state a constraint the
  code can't show (e.g. "the uuid temp name is what makes concurrent writes safe").
  Never restate what the code does, define standard terms, or explain how Python
  features work. When in doubt, delete it.
- **`__init__.py` is a pure manifest**: imports + `__all__`, zero implementation.
  Packages organize modules by concept (e.g. codecs by format), not by dependency
  tier or code size.
- **Codec mental model**: a codec is how one in-memory value becomes one file and
  back. `Codec` is generic over the file's whole value — `Codec[M]` is a
  single-model file, `Codec[list[M]]` a dataset. List-ness is part of the value
  type, not a separate concept.
- **Optional heavy deps (pyarrow, torch) are imported lazily inside methods**, so
  `import plum` stays cheap and works without extras. Keep it that way in new
  codecs.
- **Escape hatches are all-or-nothing.** Options that must agree travel as one
  object (see `RowConverter`), never as separate kwargs a caller can half-override.
  A codec only grows customization hooks if they sit inside machinery worth
  reusing; when the codec is trivial, "write your own codec" is the escape hatch.
- All file writes go through `write_atomic`.
- Errors that reject a name should list the known names (see `UnknownName`).
