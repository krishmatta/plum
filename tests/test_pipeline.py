import json

import pytest
from pydantic import BaseModel, ValidationError

from plum import (
    Artifact,
    Catalog,
    JsonModelCodec,
    JsonlCodec,
    ParamsMismatch,
    Pipeline,
    PriorRunFailed,
    Store,
    UnknownArtifact,
    load_manifest,
)


class Payload(BaseModel):
    value: int


def build_store(tmp_path):
    catalog = Catalog()
    catalog.register(Artifact("thing", JsonModelCodec(Payload)))
    catalog.register(Artifact("scratchable", JsonlCodec(Payload)))
    return Store(catalog, tmp_path)


class Thing(Pipeline):
    name = "thing"
    produces = "thing"

    class Params(Pipeline.Params):
        value: int = 1

    def __init__(self, store):
        super().__init__(store)
        self.calls = 0

    def _run(self, ctx):
        self.calls += 1
        ctx.stats["value"] = ctx.params.value
        ctx.output(Payload(value=ctx.params.value))


def test_successful_run(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    manifest = pipe.run("r1", value=7)
    assert manifest.status == "ok"
    assert manifest.params == {"value": 7}
    assert manifest.stats == {"value": 7}
    assert manifest.started_at
    assert manifest.finished_at
    assert store.read("thing", "r1") == Payload(value=7)


def test_second_run_skips(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    pipe.run("r1", value=3)
    pipe.run("r1", value=3)
    assert pipe.calls == 1


def test_force_reexecutes(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    pipe.run("r1", value=3)
    pipe.run("r1", value=9, force=True)
    assert pipe.calls == 2
    assert store.read("thing", "r1") == Payload(value=9)


def test_rerun_with_different_params_raises(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    pipe.run("r1", value=3)
    with pytest.raises(ParamsMismatch) as exc:
        pipe.run("r1", value=4)
    assert pipe.calls == 1
    assert "value" in str(exc.value)


def test_resume_after_failure_with_different_params_raises(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    run_dir = store.run_dir("thing", "r1")
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pipeline": "thing",
                "run_id": "r1",
                "uuid": "u1",
                "status": "error",
                "params": {"value": 3},
            }
        )
    )
    with pytest.raises(ParamsMismatch):
        pipe.run("r1", value=4, resume=True)
    assert pipe.calls == 0


def test_force_with_different_params_reexecutes(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    pipe.run("r1", value=3)
    pipe.run("r1", value=4, force=True)
    assert pipe.calls == 2
    assert store.read("thing", "r1") == Payload(value=4)


def test_interrupted_run_with_different_params_raises(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    run_dir = store.run_dir("thing", "r1")
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pipeline": "thing",
                "run_id": "r1",
                "uuid": "u1",
                "status": "running",
                "params": {"value": 5},
            }
        )
    )
    with pytest.raises(ParamsMismatch):
        pipe.run("r1", value=6)
    assert pipe.calls == 0


def test_interrupted_run_resumes(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    run_dir = store.run_dir("thing", "r1")
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pipeline": "thing",
                "run_id": "r1",
                "uuid": "u1",
                "status": "running",
                "params": {"value": 5},
            }
        )
    )
    pipe.run("r1", value=5)
    assert pipe.calls == 1
    assert load_manifest(run_dir / "manifest.json").status == "ok"


class Boom(Pipeline):
    name = "boom"
    produces = "thing"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        raise RuntimeError("kaboom")


def test_error_manifest(tmp_path):
    store = build_store(tmp_path)
    pipe = Boom(store)
    with pytest.raises(RuntimeError):
        pipe.run("r1")
    manifest = load_manifest(store.run_dir("thing", "r1") / "manifest.json")
    assert manifest.status == "error"
    assert "kaboom" in manifest.error


class Flaky(Pipeline):
    name = "flaky"
    produces = "thing"

    class Params(Pipeline.Params):
        pass

    def __init__(self, store):
        super().__init__(store)
        self.calls = 0

    def _run(self, ctx):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("kaboom")
        ctx.output(Payload(value=1))


def test_error_rerun_requires_resume(tmp_path):
    store = build_store(tmp_path)
    pipe = Flaky(store)
    with pytest.raises(RuntimeError):
        pipe.run("r1")
    with pytest.raises(PriorRunFailed) as exc:
        pipe.run("r1")
    assert pipe.calls == 1
    assert "kaboom" in str(exc.value)


def test_error_rerun_with_resume(tmp_path):
    store = build_store(tmp_path)
    pipe = Flaky(store)
    with pytest.raises(RuntimeError):
        pipe.run("r1")
    manifest = pipe.run("r1", resume=True)
    assert pipe.calls == 2
    assert manifest.status == "ok"


def test_error_rerun_with_force(tmp_path):
    store = build_store(tmp_path)
    pipe = Flaky(store)
    with pytest.raises(RuntimeError):
        pipe.run("r1")
    manifest = pipe.run("r1", force=True)
    assert pipe.calls == 2
    assert manifest.status == "ok"


def test_unknown_param_raises(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    with pytest.raises(ValidationError):
        pipe.run("r1", nope=1)


class Scratcher(Pipeline):
    name = "scratcher"
    produces = "thing"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        ctx.scratch("side", [Payload(value=1)], JsonlCodec(Payload))
        ctx.output(Payload(value=0))


def test_scratch_writes_file(tmp_path):
    store = build_store(tmp_path)
    pipe = Scratcher(store)
    pipe.run("r1")
    assert (store.run_dir("thing", "r1") / "side.jsonl").exists()


class Sharded(Pipeline):
    name = "sharded"
    produces = "scratchable"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        items = [Payload(value=i) for i in range(5)]
        shards = ctx.shards(len(items), 2, JsonlCodec(Payload))
        for idx, sl in shards.pending:
            shards.write(idx, items[sl])
        shards.finalize(ctx.output_path())


def test_sharded_pipeline_finalizes_to_output_path(tmp_path):
    store = build_store(tmp_path)
    pipe = Sharded(store)
    pipe.run("r1")
    assert store.read("scratchable", "r1") == [Payload(value=i) for i in range(5)]


class Scoped(Pipeline):
    name = "scoped"
    produces = "thing"

    class Params(Pipeline.Params):
        group: str

    def scope(self, params):
        return params.group

    def _run(self, ctx):
        ctx.output(Payload(value=1))


def test_scope_groups_path(tmp_path):
    store = build_store(tmp_path)
    pipe = Scoped(store)
    manifest = pipe.run("r1", group="g1")
    assert (tmp_path / "thing" / "g1" / "r1" / "thing.json").exists()
    assert manifest.scope == "g1"
    assert load_manifest(tmp_path / "thing" / "g1" / "r1" / "manifest.json").scope == "g1"


def test_unscoped_run_records_null_scope(tmp_path):
    store = build_store(tmp_path)
    manifest = Thing(store).run("r1")
    assert manifest.scope is None


def test_list_runs(tmp_path):
    store = build_store(tmp_path)
    pipe = Thing(store)
    assert pipe.list_runs(None) == []
    pipe.run("r1")
    pipe.run("r2")
    assert pipe.list_runs(None) == ["r1", "r2"]


class Undeclared(Pipeline):
    name = "undeclared"
    produces = "unregistered"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        pass


def test_undeclared_produces_fails_before_touching_disk(tmp_path):
    store = build_store(tmp_path)
    pipe = Undeclared(store)
    with pytest.raises(UnknownArtifact):
        pipe.run("r1")
    assert list(tmp_path.iterdir()) == []
