from __future__ import annotations

from typing import Optional

import typer
from pydantic import ValidationError

from plum.catalog import Catalog, Store
from plum.config import parse_kw
from plum.errors import PlumError
from plum.pipeline import Pipeline
from plum.registry import Registry


def build_cli(
    *,
    catalog: Catalog,
    pipelines: Registry[type[Pipeline]],
    sources: dict[str, Registry] | None = None,
    data_root_default: str = "data",
) -> typer.Typer:
    """The whole generic CLI over a project's registries: run, pipelines
    list/params, runs, and a `list` subcommand per source family."""
    app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)

    @app.command()
    def run(
        pipeline: str,
        run_id: str,
        param: list[str] = typer.Argument(None, help="Params as key=value (values may be JSON)"),
        force: bool = typer.Option(False, "--force"),
        resume: bool = typer.Option(False, "--resume"),
        data_root: str = typer.Option(data_root_default, "--data-root"),
    ) -> None:
        try:
            pipeline_cls = pipelines.get(pipeline)
            store = Store(catalog, data_root)
            pipe = pipeline_cls(store)
            kw = parse_kw(param)
            manifest = pipe.run(run_id, force=force, resume=resume, **kw)
            out = store.path(pipe.produces, run_id, scope=pipe.scope(pipe.Params(**kw)))
        except (PlumError, ValidationError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        typer.echo(f"{pipeline} run '{run_id}': {manifest.status}")
        typer.echo(str(out))

    pipelines_app = typer.Typer(no_args_is_help=True)
    app.add_typer(pipelines_app, name="pipelines")

    @pipelines_app.command("list")
    def pipelines_list() -> None:
        for name in pipelines.names():
            typer.echo(name)

    @pipelines_app.command("params")
    def pipelines_params(name: str) -> None:
        try:
            pipeline_cls = pipelines.get(name)
        except PlumError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        for field_name, field in pipeline_cls.Params.model_fields.items():
            type_name = getattr(field.annotation, "__name__", str(field.annotation))
            default = "(required)" if field.is_required() else f"= {field.default!r}"
            typer.echo(f"{field_name}: {type_name} {default}")

    @app.command()
    def runs(
        pipeline: str,
        scope: Optional[str] = typer.Argument(None),
        data_root: str = typer.Option(data_root_default, "--data-root"),
    ) -> None:
        try:
            pipeline_cls = pipelines.get(pipeline)
        except PlumError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        pipe = pipeline_cls(Store(catalog, data_root))
        for run_id in pipe.list_runs(scope):
            typer.echo(run_id)

    for family, registry in (sources or {}).items():
        app.add_typer(_source_app(registry), name=family)

    return app


def _source_app(registry: Registry) -> typer.Typer:
    sub = typer.Typer(no_args_is_help=True)

    @sub.command("list")
    def source_list() -> None:
        for source_id, source in registry.items():
            display = getattr(source, "display_name", "")
            if display and display != source_id:
                typer.echo(f"{source_id}\t{display}")
            else:
                typer.echo(source_id)

    return sub
