from pathlib import Path

import pytest
from typer.testing import CliRunner

import plum.__main__ as plum_main
from plum import scaffold
from plum.scaffold import ScaffoldError

runner = CliRunner()


def fake_uv_project(root: Path, name: str = "demo") -> Path:
    """Mimic what `uv init --package <name>` leaves on disk."""
    pkg = root / "src" / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'def main() -> None:\n    print("Hello from {name}!")\n')
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n\n'
        f'[project.scripts]\n{name} = "{name}:main"\n'
    )
    return root


def test_render_templates_strips_tmpl_suffix(tmp_path):
    scaffold._render_templates(tmp_path, "demo")
    assert (tmp_path / "app.py").exists()
    assert (tmp_path / "catalog.py").exists()
    assert (tmp_path / "pipelines" / "example.py").exists()
    assert not list(tmp_path.rglob("*.tmpl"))


def test_render_templates_uses_relative_imports(tmp_path):
    scaffold._render_templates(tmp_path, "demo")
    assert "from .catalog import CATALOG" in (tmp_path / "app.py").read_text()
    assert "from ..registries import PIPELINES" in (
        tmp_path / "pipelines" / "example.py"
    ).read_text()


def test_repoint_script_rewrites_entry(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project.scripts]\ndemo = "demo:main"\n')
    scaffold._repoint_script(pyproject, "demo")
    text = pyproject.read_text()
    assert 'demo = "demo.app:app"' in text
    assert '"demo:main"' not in text


def test_repoint_script_is_idempotent(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project.scripts]\ndemo = "demo.app:app"\n')
    scaffold._repoint_script(pyproject, "demo")  # no stub, but already pointed: no-op
    assert 'demo = "demo.app:app"' in pyproject.read_text()


def test_repoint_script_missing_entry_raises(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\n')
    with pytest.raises(ScaffoldError):
        scaffold._repoint_script(pyproject, "demo")


def test_init_scaffolds_into_project(tmp_path):
    fake_uv_project(tmp_path)
    package = scaffold.init(tmp_path)
    assert package == "demo"

    pkg = tmp_path / "src" / "demo"
    assert (pkg / "app.py").exists()
    assert (pkg / "pipelines" / "example.py").exists()
    assert "Hello from" not in (pkg / "__init__.py").read_text()

    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert 'demo = "demo.app:app"' in pyproject
    assert '"demo:main"' not in pyproject


def test_init_requires_src_package(tmp_path):
    with pytest.raises(ScaffoldError):
        scaffold.init(tmp_path)


def test_init_missing_pyproject_writes_nothing(tmp_path):
    pkg = tmp_path / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    with pytest.raises(ScaffoldError):
        scaffold.init(tmp_path)
    assert not (pkg / "app.py").exists()
    assert not (pkg / "pipelines").exists()


def test_init_no_script_entry_writes_nothing(tmp_path):
    pkg = tmp_path / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    with pytest.raises(ScaffoldError):
        scaffold.init(tmp_path)
    assert not (pkg / "app.py").exists()
    assert not (pkg / "catalog.py").exists()
    assert not (pkg / "pipelines").exists()


def test_init_refuses_overwrite_without_force(tmp_path):
    fake_uv_project(tmp_path)
    scaffold.init(tmp_path)
    with pytest.raises(ScaffoldError):
        scaffold.init(tmp_path)


def test_init_force_reruns_cleanly(tmp_path):
    fake_uv_project(tmp_path)
    scaffold.init(tmp_path)
    assert scaffold.init(tmp_path, force=True) == "demo"
    assert 'demo = "demo.app:app"' in (tmp_path / "pyproject.toml").read_text()


def test_cli_init_reports_missing_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(plum_main.app, ["init"])
    assert result.exit_code == 1
    assert "src/" in result.output


def test_cli_init_scaffolds_cwd(tmp_path, monkeypatch):
    fake_uv_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(plum_main.app, ["init"])
    assert result.exit_code == 0
    assert "scaffolded plum project 'demo'" in result.output
    assert (tmp_path / "src" / "demo" / "app.py").exists()
