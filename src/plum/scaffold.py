from __future__ import annotations

from importlib import resources
from importlib.abc import Traversable
from pathlib import Path

from plum.errors import PlumError


class ScaffoldError(PlumError):
    pass


def init(project: Path | str | None = None, *, force: bool = False) -> str:
    """Scaffold plum's files into an existing project and return the package name.

    Expects a uv-style project (`uv init --package <name>`): a single package
    under `src/` and a `pyproject.toml`. Renders the templates into that package
    and points its console script at the app. Does not invoke uv.

    Every precondition is checked before anything is written, so a project in a
    bad state fails without leaving a half-written scaffold behind.
    """
    project = Path(project) if project is not None else Path.cwd()
    pkg_dir = _preflight(project, force=force)
    _render_templates(pkg_dir, pkg_dir.name)
    _repoint_script(project / "pyproject.toml", pkg_dir.name)
    _ignore_data(project / ".gitignore")
    return pkg_dir.name


def _preflight(project: Path, *, force: bool) -> Path:
    """Validate the whole operation up front; raise before any write. Returns the package dir."""
    pkg_dir = _find_package(project)
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        raise ScaffoldError(
            f"no pyproject.toml under {project}; run `uv init --package <name>` first"
        )
    if not force and (pkg_dir / "app.py").exists():
        raise ScaffoldError(f"{pkg_dir / 'app.py'} already exists; pass --force to overwrite")
    _assert_script_present(pyproject.read_text(encoding="utf-8"), pkg_dir.name)
    return pkg_dir


def _find_package(project: Path) -> Path:
    src = project / "src"
    if not src.is_dir():
        raise ScaffoldError(
            f"no src/ directory under {project}; run `uv init --package <name>` first"
        )
    packages = [p for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()]
    if len(packages) != 1:
        found = ", ".join(sorted(p.name for p in packages)) or "(none)"
        raise ScaffoldError(f"expected exactly one package under {src}; found: {found}")
    return packages[0]


def _assert_script_present(pyproject_text: str, package: str) -> None:
    if f'"{package}.app:app"' in pyproject_text or f'"{package}:main"' in pyproject_text:
        return
    raise ScaffoldError(
        f"could not find a console-script entry for '{package}' in pyproject.toml; "
        f'set [project.scripts] {package} = "{package}.app:app" manually'
    )


def _render_templates(dest: Path, package: str) -> None:
    _render_tree(resources.files("plum") / "_templates", dest, package)


def _render_tree(src: Traversable, dest: Path, package: str) -> None:
    for item in src.iterdir():
        if item.is_dir():
            _render_tree(item, dest / item.name, package)
            continue
        target = dest / item.name.removesuffix(".tmpl")
        target.parent.mkdir(parents=True, exist_ok=True)
        text = item.read_text(encoding="utf-8").replace("{{ package }}", package)
        target.write_text(text, encoding="utf-8")


def _ignore_data(gitignore: Path) -> None:
    entry = "data/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry in existing.splitlines():
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with gitignore.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}{entry}\n")


def _repoint_script(pyproject: Path, package: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    app_target = f'"{package}.app:app"'
    if app_target in text:
        return
    stub = f'"{package}:main"'
    if stub not in text:
        raise ScaffoldError(
            f"could not find a console-script entry for '{package}' in {pyproject}; "
            f'set [project.scripts] {package} = "{package}.app:app" manually'
        )
    pyproject.write_text(text.replace(stub, app_target), encoding="utf-8")
