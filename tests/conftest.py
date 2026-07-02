import pytest


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path_factory, monkeypatch):
    # Pipeline.run inspects Path.cwd() for git provenance. Keep tests out of the
    # plum repo so they neither record its commit nor refuse on its dirty state;
    # the provenance tests opt back into a real repo by chdir-ing themselves.
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
