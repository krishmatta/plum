from typer.testing import CliRunner

from plum import Pipeline, Registry, RunManifest, Store, build_cli, load_manifest

from tests.example.app import app
from tests.example.catalog import CATALOG
from tests.example.schema import Numbers, Power

runner = CliRunner()


def run(*args, data_root):
    return runner.invoke(app, [*args, "--data-root", str(data_root)])


def test_run_load_writes_artifact(tmp_path):
    result = run("run", "load", "r1", "n=3", data_root=tmp_path)
    assert result.exit_code == 0, result.output
    store = Store(CATALOG, tmp_path)
    assert store.read("numbers", "r1") == Numbers(values=[0, 1, 2])


def test_run_apply_consumes_upstream_scoped_by_method(tmp_path):
    run("run", "load", "nums", "n=6", data_root=tmp_path)
    result = run("run", "apply", "p1", "numbers_run=nums", "method=cube", data_root=tmp_path)
    assert result.exit_code == 0, result.output
    store = Store(CATALOG, tmp_path)
    assert store.read("powers", "p1", scope="cube") == [Power(x=x, y=x**3) for x in range(6)]
    assert (tmp_path / "powers" / "cube" / "p1" / "powers.jsonl").exists()


def test_rerun_load_skips(tmp_path):
    run("run", "load", "r1", data_root=tmp_path)
    manifest_path = Store(CATALOG, tmp_path).run_dir("numbers", "r1") / "manifest.json"
    first = load_manifest(manifest_path).finished_at
    run("run", "load", "r1", data_root=tmp_path)
    assert load_manifest(manifest_path).finished_at == first


def test_unknown_pipeline_lists_known(tmp_path):
    result = run("run", "nope", "r1", data_root=tmp_path)
    assert result.exit_code == 1
    assert "load" in result.output and "apply" in result.output


def test_bad_param_exits_nonzero(tmp_path):
    result = run("run", "load", "r1", "n=notanint", data_root=tmp_path)
    assert result.exit_code == 1


def test_pipelines_list():
    result = runner.invoke(app, ["pipelines", "list"])
    assert result.exit_code == 0
    assert "load" in result.output
    assert "apply" in result.output


def test_pipelines_params_apply():
    result = runner.invoke(app, ["pipelines", "params", "apply"])
    assert result.exit_code == 0
    assert "numbers_run: str (required)" in result.output
    assert "method: str = 'square'" in result.output


def test_runs_lists_run_ids_in_scope(tmp_path):
    run("run", "load", "nums", data_root=tmp_path)
    run("run", "apply", "a", "numbers_run=nums", "method=cube", data_root=tmp_path)
    run("run", "apply", "b", "numbers_run=nums", "method=cube", data_root=tmp_path)
    result = run("runs", "apply", "cube", data_root=tmp_path)
    assert result.exit_code == 0
    ids = [line.split()[0] for line in result.output.splitlines()[1:]]
    assert ids == ["a", "b"]


def test_runs_shows_header_status_and_duration(tmp_path):
    run("run", "load", "r1", "n=3", data_root=tmp_path)
    result = run("runs", "load", data_root=tmp_path)
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0].split() == ["RUN", "ID", "STATUS", "STARTED", "FINISHED", "DURATION"]
    assert "r1" in lines[1]
    assert "ok" in lines[1]


def test_runs_empty_reports_no_runs(tmp_path):
    result = run("runs", "load", data_root=tmp_path)
    assert result.exit_code == 0
    assert result.output.strip() == "no runs"


def test_runs_marks_missing_manifest(tmp_path):
    (tmp_path / "numbers" / "ghost").mkdir(parents=True)
    result = run("runs", "load", data_root=tmp_path)
    assert result.exit_code == 0
    assert "ghost" in result.output
    assert "(no manifest)" in result.output


def test_show_dumps_manifest_as_json(tmp_path):
    run("run", "load", "r1", "n=3", data_root=tmp_path)
    result = run("show", "load", "r1", data_root=tmp_path)
    assert result.exit_code == 0
    m = RunManifest.model_validate_json(result.output)
    assert m.run_id == "r1"
    assert m.status == "ok"


def test_show_scoped_run(tmp_path):
    run("run", "load", "nums", data_root=tmp_path)
    run("run", "apply", "p1", "numbers_run=nums", "method=cube", data_root=tmp_path)
    result = run("show", "apply", "p1", "cube", data_root=tmp_path)
    assert result.exit_code == 0
    assert RunManifest.model_validate_json(result.output).name == "apply"


def test_show_unknown_run_exits_nonzero(tmp_path):
    result = run("show", "load", "nope", data_root=tmp_path)
    assert result.exit_code == 1
    assert "no manifest" in result.output


def test_methods_list_shows_display_name():
    result = runner.invoke(app, ["methods", "list"])
    assert result.exit_code == 0
    assert result.output == "cube\tCube (x³)\nsquare\n"


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
