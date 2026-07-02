import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from plum import (
    Artifact,
    Catalog,
    JsonModelCodec,
    Pipeline,
    Registry,
    Runner,
    Store,
    UnknownName,
    load_manifest,
    sweep,
)

from tests.example.app import app

runner = CliRunner()


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
        app, ["experiments", "run", "method-sweep", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    # the shared upstream ran once
    base = load_manifest(tmp_path / "numbers" / "base" / "manifest.json")
    assert base.description == "shared numbers"
    # both swept downstream runs exist, scoped by method
    assert (tmp_path / "powers" / "square" / "pow-square" / "manifest.json").exists()
    assert (tmp_path / "powers" / "cube" / "pow-cube" / "manifest.json").exists()
    cube = load_manifest(tmp_path / "powers" / "cube" / "pow-cube" / "manifest.json")
    assert cube.description == "powers via cube"
    assert cube.inputs[0].run_id == "base"  # lineage points at the shared upstream


def test_experiments_run_unknown_exits_nonzero(tmp_path):
    result = runner.invoke(
        app, ["experiments", "run", "nope", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 1
