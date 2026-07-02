import pytest
from pydantic import BaseModel

from plum import (
    Artifact,
    Catalog,
    JsonModelCodec,
    Pipeline,
    Store,
    load_manifest,
)


class Payload(BaseModel):
    value: int


def build_store(tmp_path):
    catalog = Catalog()
    catalog.register(Artifact("src", JsonModelCodec(Payload)))
    catalog.register(Artifact("out", JsonModelCodec(Payload)))
    return Store(catalog, tmp_path)


class Producer(Pipeline):
    name = "producer"
    produces = "src"

    class Params(Pipeline.Params):
        value: int = 5

    def _run(self, ctx):
        ctx.output(Payload(value=ctx.params.value))


class Consumer(Pipeline):
    name = "consumer"
    produces = "out"

    class Params(Pipeline.Params):
        src_run: str
        twice: bool = False

    def _run(self, ctx):
        p = ctx.read("src", ctx.params.src_run)
        if ctx.params.twice:
            ctx.read("src", ctx.params.src_run)  # same edge again
        ctx.output(Payload(value=p.value))


def edge(ref):
    return (ref.artifact, ref.run_id, ref.scope)


def test_read_records_input_edge(tmp_path):
    store = build_store(tmp_path)
    produced = Producer(store).run("s1", value=7)
    manifest = Consumer(store).run("c1", src_run="s1")
    assert len(manifest.inputs) == 1
    ref = manifest.inputs[0]
    assert edge(ref) == ("src", "s1", None)
    assert ref.produced_at == produced.finished_at  # the exact generation read


def test_repeated_read_is_deduped(tmp_path):
    store = build_store(tmp_path)
    Producer(store).run("s1")
    manifest = Consumer(store).run("c1", src_run="s1", twice=True)
    assert len(manifest.inputs) == 1


def test_no_reads_gives_empty_inputs(tmp_path):
    store = build_store(tmp_path)
    manifest = Producer(store).run("s1")
    assert manifest.inputs == []


def test_inputs_persist_to_manifest_file(tmp_path):
    store = build_store(tmp_path)
    Producer(store).run("s1")
    Consumer(store).run("c1", src_run="s1")
    reloaded = load_manifest(tmp_path / "out" / "c1" / "manifest.json")
    assert edge(reloaded.inputs[0]) == ("src", "s1", None)


def test_failed_read_records_nothing(tmp_path):
    store = build_store(tmp_path)
    with pytest.raises(Exception):
        Consumer(store).run("c1", src_run="missing")
    errored = load_manifest(tmp_path / "out" / "c1" / "manifest.json")
    assert errored.status == "error"
    assert errored.inputs == []


def test_stamp_detects_upstream_regeneration(tmp_path):
    # a -> b, then regenerate a with the same id: b's input must not silently
    # appear to reference the new a.
    store = build_store(tmp_path)
    original = Producer(store).run("s1", value=1)
    b = Consumer(store).run("c1", src_run="s1")
    assert b.inputs[0].produced_at == original.finished_at

    regenerated = Producer(store).run("s1", value=2, force=True)
    assert regenerated.finished_at != original.finished_at
    # b's recorded stamp still points at the generation it actually read,
    # which no longer matches the current upstream -> staleness is detectable.
    current = load_manifest(tmp_path / "src" / "s1" / "manifest.json")
    assert b.inputs[0].produced_at != current.finished_at
