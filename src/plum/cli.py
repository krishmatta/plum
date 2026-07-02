from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

import click
import typer
from pydantic import ValidationError

from plum.catalog import Catalog, Store
from plum.config import parse_kw
from plum.errors import PlumError
from plum.experiment import Runner
from plum.pipeline import Pipeline
from plum.registry import Registry


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


def _fmt_duration(started: str | None, finished: str | None) -> str:
    if not started or not finished:
        return "-"
    try:
        secs = int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return "-"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60}s"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _gather_description(
    pipeline: str, run_id: str, params: dict, message: str | None
) -> str | None:
    """The run message. -m wins; at a terminal an empty -m opens $EDITOR and an
    empty result aborts (git-style); non-interactively it's optional."""
    if message is not None:
        described = message.strip()
        if not described:
            raise ValueError("empty run description; write a non-empty -m message")
        return described
    if not sys.stdin.isatty():
        return None  # scripts/CI pass -m (or a programmatic description); don't block
    template = (
        "\n"
        f"# Describe run '{run_id}' of pipeline '{pipeline}'.\n"
        "# Lines starting with '#' are ignored; an empty message aborts the run.\n"
        f"# params: {params}\n"
    )
    edited = click.edit(template)
    if edited is None:
        raise ValueError("aborted: no run description written")
    described = "\n".join(
        line for line in edited.splitlines() if not line.startswith("#")
    ).strip()
    if not described:
        raise ValueError("aborted: empty run description")
    return described


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(cell) for cell in col) for col in zip(*([headers] + rows))]
    for row in [headers, *rows]:
        typer.echo("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


def build_cli(
    *,
    catalog: Catalog,
    pipelines: Registry[type[Pipeline]],
    sources: dict[str, Registry] | None = None,
    experiments: Registry | None = None,
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
        message: Optional[str] = typer.Option(
            None, "-m", "--message", help="Run description; opens $EDITOR if omitted at a terminal"
        ),
        force: bool = typer.Option(False, "--force"),
        resume: bool = typer.Option(False, "--resume"),
        data_root: str = typer.Option(data_root_default, "--data-root"),
    ) -> None:
        try:
            pipeline_cls = pipelines.get(pipeline)
            store = Store(catalog, data_root)
            pipe = pipeline_cls(store)
            kw = parse_kw(param)
            scope = pipe.scope(pipe.Params(**kw))
            # Only ask for a description when creating a new (or forced) run, so
            # cached reruns don't re-prompt.
            description = None
            if force or pipe.manifest(run_id, scope=scope) is None:
                description = _gather_description(pipeline, run_id, kw, message)
            manifest = pipe.run(
                run_id, force=force, resume=resume, description=description, **kw
            )
            out = store.path(pipe.produces, run_id, scope=scope)
        except (PlumError, ValidationError, ValueError) as e:
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
        run_ids = pipe.list_runs(scope)
        if not run_ids:
            typer.echo("no runs")
            return
        rows = []
        for run_id in run_ids:
            m = pipe.manifest(run_id, scope=scope)
            if m is None:
                rows.append([run_id, "(no manifest)", "-", "-", "-"])
            else:
                rows.append(
                    [
                        run_id,
                        m.status,
                        _fmt_ts(m.started_at),
                        _fmt_ts(m.finished_at),
                        _fmt_duration(m.started_at, m.finished_at),
                    ]
                )
        _print_table(["RUN ID", "STATUS", "STARTED", "FINISHED", "DURATION"], rows)

    @app.command()
    def show(
        pipeline: str,
        run_id: str,
        scope: Optional[str] = typer.Argument(None),
        data_root: str = typer.Option(data_root_default, "--data-root"),
    ) -> None:
        try:
            pipeline_cls = pipelines.get(pipeline)
        except PlumError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        pipe = pipeline_cls(Store(catalog, data_root))
        m = pipe.manifest(run_id, scope=scope)
        if m is None:
            typer.echo(f"no manifest for run '{run_id}'", err=True)
            raise typer.Exit(1)
        typer.echo(m.model_dump_json(indent=2))

    for family, registry in (sources or {}).items():
        app.add_typer(_source_app(registry), name=family)

    if experiments is not None:
        app.add_typer(
            _experiments_app(experiments, catalog, pipelines, data_root_default),
            name="experiments",
        )

    return app


def _experiments_app(
    experiments: Registry, catalog: Catalog, pipelines: Registry, data_root_default: str
) -> typer.Typer:
    sub = typer.Typer(no_args_is_help=True)

    @sub.command("list")
    def experiments_list() -> None:
        for experiment_id in experiments.names():
            typer.echo(experiment_id)

    @sub.command("run")
    def experiments_run(
        experiment: str,
        data_root: str = typer.Option(data_root_default, "--data-root"),
    ) -> None:
        try:
            experiment_cls = experiments.get(experiment)
            runner = Runner(pipelines, Store(catalog, data_root))
            experiment_cls().run(runner)
        except (PlumError, ValidationError, ValueError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        typer.echo(f"experiment '{experiment}': done")

    return sub


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
