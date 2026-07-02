from __future__ import annotations

import typer

from plum import scaffold
from plum.errors import PlumError

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


@app.callback()
def main() -> None:
    """plum project tooling."""


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing scaffold files."),
) -> None:
    """Scaffold plum's files into the current project.

    Run inside a uv project (uv init --package <name> && uv add plum).
    """
    try:
        package = scaffold.init(force=force)
    except PlumError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"scaffolded plum project '{package}'")
    typer.echo(f"next: uv run {package} run load r1 n=5")


if __name__ == "__main__":
    app()
