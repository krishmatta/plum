import shutil
import subprocess

import pytest
from pydantic import BaseModel

from plum import Artifact, Catalog, DirtyWorkingTree, Pipeline, Store
from plum.codecs import JsonModelCodec

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


class Blob(BaseModel):
    n: int


class Emit(Pipeline):
    name = "emit"
    produces = "blob"

    class Params(Pipeline.Params):
        n: int = 1

    def _run(self, ctx):
        ctx.output(Blob(n=ctx.params.n))


def catalog() -> Catalog:
    cat = Catalog()
    cat.register(Artifact("blob", JsonModelCodec(Blob)))
    return cat


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def make_repo(root):
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    # hermetic against a host global config that signs commits
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("/data/\n")
    (root / "code.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "init")


def test_clean_repo_records_sha(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    manifest = Emit(Store(catalog(), tmp_path / "data")).run("r1")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert manifest.git is not None
    assert manifest.git.sha == head
    assert manifest.git.branch


def test_dirty_tracked_change_refuses(tmp_path, monkeypatch):
    make_repo(tmp_path)
    (tmp_path / "code.py").write_text("x = 2\n")  # modify a tracked file
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DirtyWorkingTree):
        Emit(Store(catalog(), tmp_path / "data")).run("r1")
    assert not (tmp_path / "data").exists()  # nothing written


def test_untracked_file_refuses(tmp_path, monkeypatch):
    make_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("todo")  # untracked, not ignored
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DirtyWorkingTree):
        Emit(Store(catalog(), tmp_path / "data")).run("r1")


def test_gitignored_output_does_not_refuse(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = Store(catalog(), tmp_path / "data")  # data/ is gitignored
    Emit(store).run("r1")
    Emit(store).run("r2")  # second run: data/ now exists but is ignored -> ok
    assert store.read("blob", "r2") == Blob(n=1)


def test_no_commits_refuses(tmp_path, monkeypatch):
    git(tmp_path, "init")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DirtyWorkingTree):
        Emit(Store(catalog(), tmp_path / "data")).run("r1")


def test_non_repo_records_no_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # plain dir, not a repo
    manifest = Emit(Store(catalog(), tmp_path / "data")).run("r1")
    assert manifest.git is None
