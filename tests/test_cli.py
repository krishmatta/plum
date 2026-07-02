from typer.testing import CliRunner

from plum import Pipeline, Registry, Store, build_cli, load_manifest

from tests.example.app import CATALOG, Numbers, app

runner = CliRunner()


def run(*args, data_root):
    return runner.invoke(app, [*args, "--data-root", str(data_root)])


def test_run_load_writes_artifact(tmp_path):
    result = run("run", "load", "r1", "n=3", data_root=tmp_path)
    assert result.exit_code == 0, result.output
    store = Store(CATALOG, tmp_path)
    assert store.read("numbers", None, "r1") == Numbers(values=[0, 1, 2])


def test_run_square_consumes_upstream(tmp_path):
    run("run", "load", "nums", "n=4", data_root=tmp_path)
    result = run("run", "square", "sq", "numbers_run=nums", data_root=tmp_path)
    assert result.exit_code == 0, result.output
    store = Store(CATALOG, tmp_path)
    assert store.read("squares", None, "sq") == Numbers(values=[0, 1, 4, 9])


def test_rerun_load_skips(tmp_path):
    run("run", "load", "r1", data_root=tmp_path)
    manifest_path = Store(CATALOG, tmp_path).run_dir("numbers", None, "r1") / "manifest.json"
    first = load_manifest(manifest_path).finished_at
    run("run", "load", "r1", data_root=tmp_path)
    assert load_manifest(manifest_path).finished_at == first


def test_unknown_pipeline_lists_known(tmp_path):
    result = run("run", "nope", "r1", data_root=tmp_path)
    assert result.exit_code == 1
    assert "load" in result.output and "square" in result.output


def test_bad_param_exits_nonzero(tmp_path):
    result = run("run", "load", "r1", "n=notanint", data_root=tmp_path)
    assert result.exit_code == 1


def test_pipelines_list():
    result = runner.invoke(app, ["pipelines", "list"])
    assert result.exit_code == 0
    assert "load" in result.output
    assert "square" in result.output


def test_pipelines_params_square():
    result = runner.invoke(app, ["pipelines", "params", "square"])
    assert result.exit_code == 0
    assert "numbers_run: str (required)" in result.output


def test_runs_lists_run_ids(tmp_path):
    run("run", "load", "a", data_root=tmp_path)
    run("run", "load", "b", data_root=tmp_path)
    result = run("runs", "load", data_root=tmp_path)
    assert result.exit_code == 0
    assert result.output.split() == ["a", "b"]


def test_failed_run_requires_resume(tmp_path):
    class Boom(Pipeline):
        name = "boom"
        produces = "numbers"

        class Params(Pipeline.Params):
            pass

        def _run(self, ctx):
            raise RuntimeError("kaboom")

    boom_reg: Registry[type[Pipeline]] = Registry("pipeline", key="name")
    boom_reg.register(Boom)
    boom_app = build_cli(catalog=CATALOG, pipelines=boom_reg)

    args = ["run", "boom", "r1", "--data-root", str(tmp_path)]
    runner.invoke(boom_app, args)  # fails, writes error manifest
    result = runner.invoke(boom_app, args)
    assert result.exit_code == 1
    assert "previously failed" in result.output
    assert "resume=True" in result.output
