from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from plum import (
    BACKENDS,
    PlumError,
    Store,
    SyncConflict,
    UnknownName,
    load_manifest,
    load_remote,
    pull,
    push,
)
from plum.pipeline import MANIFEST_FILE, RunManifest

from tests.example.app import app
from tests.example.backends.folder import FolderBackend
from tests.example.catalog import CATALOG
from tests.example.registries import PIPELINES
from tests.example.schema import Numbers, Power

runner = CliRunner()


def store(root: Path) -> Store:
    return Store(CATALOG, root)


def run(root: Path, pipeline: str, run_id: str, **params) -> RunManifest:
    return PIPELINES.get(pipeline)(store(root)).run(run_id, description="x", **params)


def folder(root: Path) -> FolderBackend:
    return FolderBackend(FolderBackend.Options(path=str(root)))


def write_manifest(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    fields.setdefault("uuid", uuid4().hex)
    m = RunManifest(pipeline="load", run_id=run_dir.name, **fields)
    (run_dir / MANIFEST_FILE).write_text(m.model_dump_json())


# ---- push -------------------------------------------------------------------


def test_push_transfers_only_ok_runs(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    run(local, "load", "ok", n=3)
    write_manifest(local / "numbers" / "bad", status="error")
    write_manifest(local / "numbers" / "mid", status="running")

    push(local, folder(remote))
    assert folder(remote).list_runs() == ["numbers/ok"]


def test_push_writes_manifest_last(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    run(local, "load", "r1", n=3)

    order: list[str] = []

    class Recording(FolderBackend):
        def upload(self, src, relpath):
            order.append(relpath)
            super().upload(src, relpath)

    push(local, Recording(FolderBackend.Options(path=str(remote))))
    assert order[-1] == f"numbers/r1/{MANIFEST_FILE}"
    assert len(order) > 1  # a non-manifest artifact file preceded it


def test_push_conflict_is_all_or_nothing(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    run(local, "load", "r1", n=3)
    push(local, folder(remote))

    run(local, "load", "r1", n=3, force=True)  # new timestamps -> divergent generation
    run(local, "load", "r2", n=1)  # a fresh run that must not sneak through

    with pytest.raises(SyncConflict) as exc:
        push(local, folder(remote))
    assert "numbers/r1" in str(exc.value)
    assert folder(remote).list_runs() == ["numbers/r1"]  # r2 was not transferred


def test_push_force_overwrites_and_drops_stale_files(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    run(local, "load", "r1", n=3)
    (local / "numbers" / "r1" / "stale.txt").write_text("old")
    push(local, folder(remote))
    assert (remote / "numbers" / "r1" / "stale.txt").exists()

    run(local, "load", "r1", n=3, force=True)  # regenerated, no stale.txt
    result = push(local, folder(remote), force=True)

    assert result.forced == ["numbers/r1"]
    assert not (remote / "numbers" / "r1" / "stale.txt").exists()
    assert (remote / "numbers" / "r1" / "numbers.json").exists()
    remote_m = load_manifest(remote / "numbers" / "r1" / MANIFEST_FILE)
    local_m = load_manifest(local / "numbers" / "r1" / MANIFEST_FILE)
    assert remote_m.finished_at == local_m.finished_at


def test_push_is_idempotent(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    run(local, "load", "r1", n=3)
    push(local, folder(remote))
    result = push(local, folder(remote))
    assert result.transferred == []
    assert result.skipped == ["numbers/r1"]


def test_push_conflicts_on_same_timestamps_different_uuid(tmp_path):
    # the clock-skew case timestamps could never catch
    local, remote = tmp_path / "local", tmp_path / "remote"
    stamps = dict(
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:01:00+00:00"
    )
    write_manifest(local / "numbers" / "r1", status="ok", uuid="aaa", **stamps)
    write_manifest(remote / "numbers" / "r1", status="ok", uuid="bbb", **stamps)

    with pytest.raises(SyncConflict) as exc:
        push(local, folder(remote))
    assert "numbers/r1" in str(exc.value)


def test_push_skips_manifest_without_uuid(tmp_path):
    # a pre-0.2 manifest fails validation and is simply not a pushable run
    local, remote = tmp_path / "local", tmp_path / "remote"
    body = json.dumps(
        {
            "pipeline": "load",
            "run_id": "r1",
            "status": "ok",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:01:00+00:00",
        }
    )
    (local / "numbers" / "r1").mkdir(parents=True)
    (local / "numbers" / "r1" / MANIFEST_FILE).write_text(body)

    result = push(local, folder(remote))
    assert result.transferred == []
    assert result.skipped == []
    assert folder(remote).list_runs() == []


def test_input_ref_records_upstream_uuid(tmp_path):
    run(tmp_path, "load", "base", n=3)
    apply_m = run(tmp_path, "apply", "p1", numbers_run="base", method="cube")
    load_m = load_manifest(tmp_path / "numbers" / "base" / MANIFEST_FILE)
    assert load_m.uuid
    assert apply_m.inputs[0].uuid == load_m.uuid


# ---- pull -------------------------------------------------------------------


def test_pull_everything(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=3)
    run(src, "load", "r2", n=1)
    push(src, folder(remote))

    result = pull(dest, folder(remote))
    assert sorted(result.transferred) == ["numbers/r1", "numbers/r2"]
    assert store(dest).read("numbers", "r1") == Numbers(values=[0, 1, 2])


def test_pull_conflict_is_all_or_nothing(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=3)
    run(src, "load", "r2", n=1)
    push(src, folder(remote))

    run(dest, "load", "r1", n=3)  # local r1 diverges from remote r1

    with pytest.raises(SyncConflict):
        pull(dest, folder(remote))
    assert not (dest / "numbers" / "r2").exists()  # nothing transferred


def test_pull_force_replaces_local_run(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=5)
    push(src, folder(remote))

    run(dest, "load", "r1", n=2)  # a different local generation
    assert store(dest).read("numbers", "r1") == Numbers(values=[0, 1])

    pull(dest, folder(remote), force=True)
    assert store(dest).read("numbers", "r1") == Numbers(values=[0, 1, 2, 3, 4])


def make_flaky(remote: Path, fail_on: int) -> FolderBackend:
    class Flaky(FolderBackend):
        def __init__(self, options):
            super().__init__(options)
            self.calls = 0

        def download(self, relpath, dest_path):
            self.calls += 1
            if self.calls == fail_on:
                raise RuntimeError("connection lost")
            super().download(relpath, dest_path)

    return Flaky(FolderBackend.Options(path=str(remote)))


def test_pull_crash_leaves_nothing_at_run_path(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=6)  # two files: numbers.json + manifest.json
    push(src, folder(remote))

    with pytest.raises(RuntimeError):
        pull(dest, make_flaky(remote, fail_on=2))
    assert not (dest / "numbers").exists()
    leftovers = [p for p in dest.rglob("*") if p.is_file()]
    assert all(".tmp" in p.parts for p in leftovers)


def test_pull_retry_after_crash_succeeds(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=6)
    push(src, folder(remote))

    with pytest.raises(RuntimeError):
        pull(dest, make_flaky(remote, fail_on=2))

    result = pull(dest, folder(remote))
    assert result.transferred == ["numbers/r1"]
    assert store(dest).read("numbers", "r1") == Numbers(values=list(range(6)))


def test_force_pull_crash_preserves_local_run(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "r1", n=5)
    push(src, folder(remote))

    run(dest, "load", "r1", n=2)  # divergent local generation

    with pytest.raises(RuntimeError):
        pull(dest, make_flaky(remote, fail_on=2), force=True)
    assert store(dest).read("numbers", "r1") == Numbers(values=[0, 1])


def test_push_ignores_staging_leftovers(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    write_manifest(local / ".tmp" / "x" / "numbers" / "r1", status="ok")

    result = push(local, folder(remote))
    assert result.transferred == []
    assert folder(remote).list_runs() == []


def test_lineage_closure_pulls_upstream(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))

    result = pull(dest, folder(remote), artifact="powers", run_id="p1", scope="cube")
    assert set(result.transferred) == {"powers/cube/p1", "numbers/base"}
    assert store(dest).read("numbers", "base") == Numbers(values=list(range(6)))
    assert store(dest).read("powers", "p1", scope="cube") == [
        Power(x=x, y=x**3) for x in range(6)
    ]


def test_lineage_closure_skips_same_generation_upstream(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))

    pull(dest, folder(remote), artifact="numbers", run_id="base")  # same generation locally

    downloads: list[str] = []

    class Recording(FolderBackend):
        def download(self, relpath, dest_path):
            downloads.append(relpath)
            super().download(relpath, dest_path)

    backend = Recording(FolderBackend.Options(path=str(remote)))
    result = pull(dest, backend, artifact="powers", run_id="p1", scope="cube")

    assert result.transferred == ["powers/cube/p1"]
    assert not any(d.startswith("numbers/base") for d in downloads)


def test_lineage_closure_skips_local_only_upstream(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))
    shutil.rmtree(remote / "numbers" / "base")  # upstream absent on the remote

    run(dest, "load", "base", n=6)  # a local-only upstream is satisfied as-is

    result = pull(dest, folder(remote), artifact="powers", run_id="p1", scope="cube")
    assert result.transferred == ["powers/cube/p1"]


def test_lineage_closure_divergent_root_conflicts(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))

    run(dest, "load", "base", n=6)  # divergent generations of both members
    run(dest, "apply", "p1", numbers_run="base", method="cube")
    before = (dest / "powers" / "cube" / "p1" / MANIFEST_FILE).read_text()

    with pytest.raises(SyncConflict) as exc:
        pull(dest, folder(remote), artifact="powers", run_id="p1", scope="cube")
    assert "powers/cube/p1" in str(exc.value)
    assert (dest / "powers" / "cube" / "p1" / MANIFEST_FILE).read_text() == before


def test_lineage_closure_divergent_upstream_conflicts(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))

    run(dest, "load", "base", n=6)  # divergent upstream, root absent locally

    with pytest.raises(SyncConflict) as exc:
        pull(dest, folder(remote), artifact="powers", run_id="p1", scope="cube")
    assert "numbers/base" in str(exc.value)
    assert not (dest / "powers" / "cube" / "p1").exists()  # nothing transferred


def test_lineage_closure_force_replaces_divergent_and_pulls_rest(tmp_path):
    src, remote, dest = tmp_path / "src", tmp_path / "remote", tmp_path / "dest"
    run(src, "load", "base", n=6)
    run(src, "apply", "p1", numbers_run="base", method="cube")
    push(src, folder(remote))

    run(dest, "load", "base", n=6)  # divergent upstream

    result = pull(
        dest, folder(remote), artifact="powers", run_id="p1", scope="cube", force=True
    )
    assert result.forced == ["numbers/base"]
    assert result.transferred == ["powers/cube/p1"]
    remote_m = load_manifest(remote / "numbers" / "base" / MANIFEST_FILE)
    local_m = load_manifest(dest / "numbers" / "base" / MANIFEST_FILE)
    assert local_m.finished_at == remote_m.finished_at
    assert store(dest).read("powers", "p1", scope="cube") == [
        Power(x=x, y=x**3) for x in range(6)
    ]


def test_lineage_closure_missing_member_errors(tmp_path):
    remote, dest = tmp_path / "remote", tmp_path / "dest"
    with pytest.raises(PlumError) as exc:
        pull(dest, folder(remote), artifact="powers", run_id="ghost", scope="cube")
    assert "powers/cube/ghost" in str(exc.value)


# ---- plum.toml loading ------------------------------------------------------


def write_toml(body: str) -> Path:
    path = Path("plum.toml")
    path.write_text(body)
    return path


def test_load_remote_valid(tmp_path):
    write_toml('[remotes.origin]\nbackend = "folder"\npath = "somewhere"\n')
    backend = load_remote("origin")
    assert isinstance(backend, FolderBackend)
    assert backend.options.path == "somewhere"


def test_load_remote_missing_file(tmp_path):
    with pytest.raises(PlumError):
        load_remote("origin")


def test_load_remote_unknown_remote_lists_known(tmp_path):
    write_toml('[remotes.origin]\nbackend = "folder"\npath = "x"\n')
    with pytest.raises(UnknownName) as exc:
        load_remote("nope")
    assert "origin" in str(exc.value)


def test_load_remote_unknown_backend(tmp_path):
    write_toml('[remotes.origin]\nbackend = "nosuch"\n')
    with pytest.raises(UnknownName) as exc:
        load_remote("origin")
    assert "s3" in str(exc.value)


def test_load_remote_bad_options(tmp_path):
    write_toml('[remotes.origin]\nbackend = "folder"\n')  # missing required path
    with pytest.raises(ValidationError):
        load_remote("origin")


def test_load_remote_rejects_unknown_option_key(tmp_path):
    write_toml('[remotes.origin]\nbackend = "s3"\nbucket = "b"\nprefx = "typo"\n')
    with pytest.raises(ValidationError) as exc:
        load_remote("origin")
    assert "prefx" in str(exc.value)


# ---- CLI --------------------------------------------------------------------


def test_cli_push_and_pull(tmp_path):
    local, remote, dest = tmp_path / "local", tmp_path / "remote", tmp_path / "dest"
    write_toml(f'[remotes.origin]\nbackend = "folder"\npath = "{remote}"\n')
    run(local, "load", "r1", n=3)

    pushed = runner.invoke(app, ["push", "--data-root", str(local)])
    assert pushed.exit_code == 0, pushed.output
    assert "pushed 1" in pushed.output

    pulled = runner.invoke(app, ["pull", "--data-root", str(dest)])
    assert pulled.exit_code == 0, pulled.output
    assert "pulled 1" in pulled.output
    assert store(dest).read("numbers", "r1") == Numbers(values=[0, 1, 2])


def test_cli_pull_artifact_requires_run_id(tmp_path):
    write_toml(f'[remotes.origin]\nbackend = "folder"\npath = "{tmp_path / "r"}"\n')
    result = runner.invoke(app, ["pull", "numbers", "--data-root", str(tmp_path / "d")])
    assert result.exit_code == 1
    assert "RUN_ID" in result.output


def test_cli_push_missing_toml_exits_nonzero(tmp_path):
    result = runner.invoke(app, ["push", "--data-root", str(tmp_path / "local")])
    assert result.exit_code == 1
    assert "plum.toml" in result.output
