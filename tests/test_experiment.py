import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from plum import (
    Artifact,
    Catalog,
    Experiment,
    JsonlCodec,
    JsonModelCodec,
    ParamsMismatch,
    Pipeline,
    Registry,
    ReservedName,
    Runner,
    Store,
    UnknownName,
    load_manifest,
    sweep,
)

from tests.example.app import app
from tests.example.catalog import CATALOG
from tests.example.registries import EXPERIMENTS, PIPELINES

runner = CliRunner()


def example_runner(tmp_path):
    return Runner(PIPELINES, Store(CATALOG, tmp_path), experiments=EXPERIMENTS)


def invocation_manifest(tmp_path, experiment_id, invocation_id):
    return load_manifest(
        tmp_path / "experiments" / experiment_id / invocation_id / "manifest.json"
    )


# --- sweep -------------------------------------------------------------------

def test_sweep_cartesian_product_with_ids():
    out = list(
        sweep(
            {"a": [1, 2], "b": ["x", "y"]},
            run_id=lambda p: f"{p['a']}-{p['b']}",
        )
    )
    assert out == [
        ({"a": 1, "b": "x"}, "1-x"),
        ({"a": 1, "b": "y"}, "1-y"),
        ({"a": 2, "b": "x"}, "2-x"),
        ({"a": 2, "b": "y"}, "2-y"),
    ]


def test_sweep_single_key():
    out = list(sweep({"m": ["p", "q"]}, run_id=lambda p: p["m"]))
    assert [rid for _, rid in out] == ["p", "q"]


# --- Runner ------------------------------------------------------------------

class Payload(BaseModel):
    value: int


class Make(Pipeline):
    name = "make"
    produces = "thing"

    class Params(Pipeline.Params):
        value: int = 1

    def _run(self, ctx):
        ctx.output(Payload(value=ctx.params.value))


def build(tmp_path):
    catalog = Catalog()
    catalog.register(Artifact("thing", JsonModelCodec(Payload)))
    pipelines: Registry = Registry("pipeline", key="name")
    pipelines.register(Make)
    return Runner(pipelines, Store(catalog, tmp_path))


def test_runner_runs_pipeline_by_name(tmp_path):
    r = build(tmp_path)
    manifest = r.run("make", "r1", value=9, description="why")
    assert manifest.status == "ok"
    assert manifest.description == "why"
    assert load_manifest(tmp_path / "thing" / "r1" / "manifest.json").params == {"value": 9}


def test_runner_reuse_is_cached(tmp_path):
    r = build(tmp_path)
    first = r.run("make", "base", value=5)
    again = r.run("make", "base", value=5)  # cached no-op
    assert again.finished_at == first.finished_at


def test_runner_unknown_pipeline_raises(tmp_path):
    with pytest.raises(UnknownName):
        build(tmp_path).run("nope", "r1")


# --- CLI integration (example wires experiments=) ----------------------------

def test_experiments_list():
    result = runner.invoke(app, ["experiments", "list"])
    assert result.exit_code == 0
    assert "method-sweep" in result.output


def test_experiments_run_sweeps_over_reused_upstream(tmp_path):
    result = runner.invoke(
        app,
        ["experiments", "run", "method-sweep", "sweep1", "n=6", "--data-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    # the shared upstream ran once
    base = load_manifest(tmp_path / "numbers" / "base-n6" / "manifest.json")
    assert base.description == "shared numbers"
    # both swept downstream runs exist, scoped by method
    assert (tmp_path / "powers" / "square" / "pow-square-n6" / "manifest.json").exists()
    assert (tmp_path / "powers" / "cube" / "pow-cube-n6" / "manifest.json").exists()
    cube = load_manifest(tmp_path / "powers" / "cube" / "pow-cube-n6" / "manifest.json")
    assert cube.description == "powers via cube"
    assert cube.inputs[0].run_id == "base-n6"  # lineage points at the shared upstream
    # the invocation itself is a recorded run
    inv = invocation_manifest(tmp_path, "method-sweep", "sweep1")
    assert inv.status == "ok"
    assert inv.params == {"n": 6}


def test_experiments_run_unknown_exits_nonzero(tmp_path):
    result = runner.invoke(
        app, ["experiments", "run", "nope", "inv1", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 1


# --- invocations -------------------------------------------------------------

def test_invoke_records_ensured_runs_including_preexisting(tmp_path):
    # pre-create the shared upstream so it no-op's inside the invocation
    pre = PIPELINES.get("load")(Store(CATALOG, tmp_path)).run(
        "base-n6", n=6, description="x"
    )
    manifest = example_runner(tmp_path).invoke("method-sweep", "sweep1", n=6)

    assert manifest.status == "ok"
    assert manifest.scope == "method-sweep"
    assert manifest.params == {"n": 6}
    assert (tmp_path / "experiments" / "method-sweep" / "sweep1" / "manifest.json").exists()

    refs = {(r.artifact, r.run_id, r.scope): r.uuid for r in manifest.inputs}
    # the pre-existing run that no-op'd is still recorded, at its exact generation
    assert refs[("numbers", "base-n6", None)] == pre.uuid
    assert ("powers", "pow-square-n6", "square") in refs
    assert ("powers", "pow-cube-n6", "cube") in refs


def test_reinvoke_same_params_is_skipped(tmp_path):
    r = example_runner(tmp_path)
    first = r.invoke("method-sweep", "sweep1", n=6)
    base_finished = load_manifest(
        tmp_path / "numbers" / "base-n6" / "manifest.json"
    ).finished_at
    again = r.invoke("method-sweep", "sweep1", n=6)
    assert again.uuid == first.uuid  # same generation: the body was skipped
    assert (
        load_manifest(tmp_path / "numbers" / "base-n6" / "manifest.json").finished_at
        == base_finished
    )  # underlying pipelines were not re-run


def test_reinvoke_different_params_raises_mismatch(tmp_path):
    r = example_runner(tmp_path)
    r.invoke("method-sweep", "sweep1", n=6)
    with pytest.raises(ParamsMismatch):
        r.invoke("method-sweep", "sweep1", n=3)


def test_invoke_composition_records_child_invocation(tmp_path):
    parent = example_runner(tmp_path).invoke("compare", "cmp1")
    # each child invocation got its own manifest
    assert (tmp_path / "experiments" / "method-sweep" / "sweep-n3" / "manifest.json").exists()
    assert (tmp_path / "experiments" / "method-sweep" / "sweep-n6" / "manifest.json").exists()
    # the parent's inputs are exactly the two child invocations, not their runs
    assert all(r.artifact == "experiments" for r in parent.inputs)
    assert {(r.run_id, r.scope) for r in parent.inputs} == {
        ("sweep-n3", "method-sweep"),
        ("sweep-n6", "method-sweep"),
    }


def test_invoke_error_records_partial_inputs(tmp_path):
    experiments: Registry = Registry("experiment")

    @experiments.register
    class Boom(Experiment):
        id = "boom"

        def _run(self, runner, params):
            runner.run("load", "base", n=3, description="x")
            raise RuntimeError("boom")

    r = Runner(PIPELINES, Store(CATALOG, tmp_path), experiments=experiments)
    with pytest.raises(RuntimeError):
        r.invoke("boom", "b1")

    inv = invocation_manifest(tmp_path, "boom", "b1")
    assert inv.status == "error"
    assert [r.run_id for r in inv.inputs] == ["base"]  # ensured before the failure


def test_frame_restores_after_nested_invocation(tmp_path):
    experiments: Registry = Registry("experiment")

    @experiments.register
    class Child(Experiment):
        id = "child"

        def _run(self, runner, params):
            runner.run("load", "child-base", n=3)

    @experiments.register
    class Parent(Experiment):
        id = "parent"

        def _run(self, runner, params):
            runner.invoke("child", "c1")
            runner.run("load", "parent-extra", n=5)  # after the child popped

    r = Runner(PIPELINES, Store(CATALOG, tmp_path), experiments=experiments)
    parent = r.invoke("parent", "p1")

    # the child's frame recorded while nested; the parent's run stayed out of it
    child = invocation_manifest(tmp_path, "child", "c1")
    assert [(ref.artifact, ref.run_id) for ref in child.inputs] == [
        ("numbers", "child-base")
    ]
    # after the pop, recording resumed into the parent
    assert [(ref.artifact, ref.run_id) for ref in parent.inputs] == [
        ("experiments", "c1"),
        ("numbers", "parent-extra"),
    ]


def test_invoke_without_experiments_registry_raises(tmp_path):
    r = Runner(PIPELINES, Store(CATALOG, tmp_path))
    with pytest.raises(Exception):
        r.invoke("method-sweep", "sweep1", n=6)


def test_catalog_register_reserved_name_raises():
    with pytest.raises(ReservedName):
        Catalog().register(Artifact("experiments", JsonlCodec(BaseModel)))


def test_cli_experiments_runs_lists_invocations(tmp_path):
    example_runner(tmp_path).invoke("method-sweep", "sweep1", n=3)
    result = runner.invoke(
        app, ["experiments", "runs", "method-sweep", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "sweep1" in result.output
    assert "ok" in result.output


def test_cli_experiments_show_dumps_invocation_manifest(tmp_path):
    example_runner(tmp_path).invoke("method-sweep", "sweep1", n=3)
    result = runner.invoke(
        app,
        ["experiments", "show", "method-sweep", "sweep1", "--data-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert '"status": "ok"' in result.output
    assert '"n": 3' in result.output


def test_cli_experiments_runs_unknown_lists_known(tmp_path):
    result = runner.invoke(
        app, ["experiments", "runs", "nope", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "method-sweep" in result.output


def test_cli_runs_unknown_pipeline_lists_known(tmp_path):
    result = runner.invoke(app, ["runs", "nope", "--data-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "apply" in result.output and "load" in result.output


def test_cli_experiments_run_smoke(tmp_path):
    result = runner.invoke(
        app,
        ["experiments", "run", "method-sweep", "inv1", "n=3", "--data-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert invocation_manifest(tmp_path, "method-sweep", "inv1").params == {"n": 3}
