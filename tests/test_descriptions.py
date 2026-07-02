import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from plum import Artifact, Catalog, JsonModelCodec, Pipeline, Store, load_manifest
from plum.cli import _gather_description

from tests.example.app import app

runner = CliRunner()


# --- _gather_description unit tests -----------------------------------------

def test_message_is_used():
    assert _gather_description("p", "r", {}, "the why") == "the why"


def test_empty_message_raises():
    with pytest.raises(ValueError):
        _gather_description("p", "r", {}, "   ")


def test_non_interactive_no_message_is_optional(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert _gather_description("p", "r", {}, None) is None


def test_editor_strips_comment_lines(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("plum.cli.click.edit", lambda text: "my reason\n# a comment\n")
    assert _gather_description("p", "r", {}, None) == "my reason"


def test_editor_empty_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("plum.cli.click.edit", lambda text: "# only comments\n")
    with pytest.raises(ValueError):
        _gather_description("p", "r", {}, None)


def test_editor_closed_without_writing_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("plum.cli.click.edit", lambda text: None)
    with pytest.raises(ValueError):
        _gather_description("p", "r", {}, None)


# --- CLI integration (CliRunner is non-interactive) -------------------------

def test_run_with_message_records_description(tmp_path):
    result = runner.invoke(
        app, ["run", "load", "r1", "n=3", "-m", "baseline sweep", "--data-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_manifest(tmp_path / "numbers" / "r1" / "manifest.json").description == "baseline sweep"


def test_run_without_message_non_interactive_is_allowed(tmp_path):
    result = runner.invoke(app, ["run", "load", "r1", "--data-root", str(tmp_path)])
    assert result.exit_code == 0
    assert load_manifest(tmp_path / "numbers" / "r1" / "manifest.json").description is None


def test_empty_message_aborts_before_running(tmp_path):
    result = runner.invoke(app, ["run", "load", "r1", "-m", "  ", "--data-root", str(tmp_path)])
    assert result.exit_code == 1
    assert not (tmp_path / "numbers" / "r1").exists()


def test_cached_rerun_does_not_require_message(tmp_path):
    runner.invoke(app, ["run", "load", "r1", "-m", "first", "--data-root", str(tmp_path)])
    result = runner.invoke(app, ["run", "load", "r1", "--data-root", str(tmp_path)])
    assert result.exit_code == 0
    assert load_manifest(tmp_path / "numbers" / "r1" / "manifest.json").description == "first"


# --- library-level: description storage + resume inheritance ----------------

class Payload(BaseModel):
    value: int


def build_store(tmp_path):
    catalog = Catalog()
    catalog.register(Artifact("thing", JsonModelCodec(Payload)))
    return Store(catalog, tmp_path)


class Ok(Pipeline):
    name = "ok"
    produces = "thing"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        ctx.output(Payload(value=1))


class Boom(Pipeline):
    name = "boom"
    produces = "thing"

    class Params(Pipeline.Params):
        pass

    def _run(self, ctx):
        raise RuntimeError("kaboom")


def test_run_stores_description(tmp_path):
    manifest = Ok(build_store(tmp_path)).run("r1", description="why this run")
    assert manifest.description == "why this run"


def test_resume_inherits_prior_description(tmp_path):
    store = build_store(tmp_path)
    with pytest.raises(RuntimeError):
        Boom(store).run("r1", description="original intent")
    with pytest.raises(RuntimeError):
        Boom(store).run("r1", resume=True)  # no description passed
    assert load_manifest(tmp_path / "thing" / "r1" / "manifest.json").description == "original intent"
