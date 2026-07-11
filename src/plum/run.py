from __future__ import annotations

import abc
import shutil
import subprocess
import traceback
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, final

from pydantic import BaseModel, Field

from plum.catalog import Store
from plum.codecs import write_atomic
from plum.errors import DirtyWorkingTree, ParamsMismatch, PriorRunFailed

import plum.config

MANIFEST_FILE = "manifest.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitInfo(BaseModel):
    sha: str
    branch: str | None = None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


def capture_git(cwd: Path) -> GitInfo | None:
    """The commit a run maps to. Raise DirtyWorkingTree in a dirty repo; None
    outside one. With a clean tree and a committed uv.lock, the sha pins the
    exact code and dependencies, so nothing else needs recording."""
    if shutil.which("git") is None:
        return None
    if _git(cwd, "rev-parse", "--show-toplevel").returncode != 0:
        return None  # not inside a git repo
    repo = _git(cwd, "rev-parse", "--show-toplevel").stdout.strip()
    head = _git(cwd, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise DirtyWorkingTree(repo, "has no commits yet")
    if _git(cwd, "status", "--porcelain").stdout.strip():
        raise DirtyWorkingTree(repo, "has uncommitted changes")
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return GitInfo(sha=head.stdout.strip(), branch=branch or None)


class RunRef(BaseModel):
    """A stamped pointer to one run generation, used wherever a manifest
    references another run (a run's inputs today).

    `uuid` pins the exact generation that was read. Run ids are mutable -- a
    force-regenerated run reuses the id but rewrites its content -- so the
    reference alone can later point at a different generation than the one
    actually consumed. The pin makes that staleness detectable: it differs
    from the upstream's current manifest once the upstream is regenerated.
    `produced_at` and `git_sha` stay for humans and reproduction.
    """

    artifact: str
    run_id: str
    scope: str | None = None
    uuid: str | None = None
    produced_at: str | None = None
    git_sha: str | None = None


class RunManifest(BaseModel):
    """Reproducibility record written to every run directory as manifest.json.

    `stats` is free-form: each run reports what it produced.
    """

    name: str  # the pipeline or experiment that ran
    run_id: str
    uuid: str
    scope: str | None = None
    status: str = "running"  # running | ok | error
    description: str | None = None
    params: dict = {}
    stats: dict = {}
    git: GitInfo | None = None
    inputs: list[RunRef] = []
    started_at: str = Field(default_factory=_timestamp)
    finished_at: str | None = None
    error: str | None = None


def load_manifest(path: Path | str) -> RunManifest:
    return RunManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def list_runs(store: Store, artifact: str, scope: str | None = None) -> list[str]:
    runs_dir = store.data_root.joinpath(*[s for s in (artifact, scope) if s])
    if not runs_dir.exists():
        return []
    return sorted(p.name for p in runs_dir.iterdir() if p.is_dir())


def run_manifest(
    store: Store, artifact: str, run_id: str, *, scope: str | None = None
) -> RunManifest | None:
    path = store.run_dir(artifact, run_id, scope=scope) / MANIFEST_FILE
    if not path.exists():
        return None
    try:
        return load_manifest(path)
    except Exception:
        return None


class Runnable(abc.ABC):
    """A named, parameterized unit whose executions are recorded as runs.

    The base creates `<data_root>/<artifact>/[<scope>/]<run_id>/`, runs the
    body, and writes manifest.json. Re-running an existing run id is cheap and
    safe: completed runs are skipped, interrupted runs resume in place, error
    runs require `resume=True` to continue, and `force=True` starts over from
    scratch. Pipelines and experiments are the two subclasses.
    """

    name: ClassVar[str]  # what manifest.name and error messages cite

    class Params(plum.config.Params):
        pass

    def __init__(self, store: Store):
        self.store = store

    @property
    @abc.abstractmethod
    def _artifact(self) -> str:
        """The catalog artifact this unit's runs live under."""
        raise NotImplementedError

    def scope(self, params: BaseModel) -> str | None:
        """Override point: the path segment grouping runs, between artifact dir and run id."""
        return None

    @abc.abstractmethod
    def _execute(
        self,
        run_id: str,
        run_dir: Path,
        params: BaseModel,
        scope: str | None,
        inputs: list[RunRef],
        stats: dict,
    ) -> None:
        """Run the body. Append consumed runs to `inputs` and record to `stats`;
        both land in the manifest even if this raises (run's finally writes them)."""
        raise NotImplementedError

    @final
    def run(
        self,
        run_id: str,
        *,
        force: bool = False,
        resume: bool = False,
        description: str | None = None,
        **params,
    ) -> RunManifest:
        if not run_id:
            raise ValueError("run_id is required")
        p = self.Params(**params)
        artifact = self._artifact
        # An undeclared artifact must fail here, not hours later at output time.
        self.store.catalog.get(artifact)
        # Refuse a dirty tree before touching disk, so every run maps to a commit.
        git = capture_git(Path.cwd())
        scope = self.scope(p)
        run_dir = self.store.run_dir(artifact, run_id, scope=scope)

        carried_description: str | None = None
        if run_dir.exists():
            if force:
                shutil.rmtree(run_dir)
            else:
                manifest_path = run_dir / MANIFEST_FILE
                if manifest_path.exists():
                    existing = load_manifest(manifest_path)
                    carried_description = existing.description
                    requested = p.model_dump(mode="json")
                    if existing.params != requested:
                        raise ParamsMismatch(
                            self.name, run_id, existing.params, requested
                        )
                    if existing.status == "ok":
                        return existing  # already done; rerunning is a no-op
                    if existing.status == "error" and not resume:
                        raise PriorRunFailed(self.name, run_id, existing.error)
                # otherwise: interrupted run, resume in place
        run_dir.mkdir(parents=True, exist_ok=True)

        manifest = RunManifest(
            name=self.name,
            run_id=run_id,
            # so a RunRef can be stamped from this manifest alone
            scope=scope,
            # every execution attempt (including a resume) is a new generation
            uuid=uuidlib.uuid4().hex,
            description=description if description is not None else carried_description,
            params=p.model_dump(mode="json"),
            git=git,
        )
        self._write_manifest(run_dir, manifest)
        inputs: list[RunRef] = []
        stats: dict = {}
        try:
            self._execute(run_id, run_dir, p, scope, inputs, stats)
            manifest.status = "ok"
        except Exception:
            manifest.status = "error"
            manifest.error = traceback.format_exc()
            raise
        finally:
            manifest.stats = stats
            manifest.inputs = inputs
            manifest.finished_at = _timestamp()
            self._write_manifest(run_dir, manifest)
        return manifest

    @staticmethod
    def _write_manifest(run_dir: Path, manifest: RunManifest) -> None:
        write_atomic(
            run_dir / MANIFEST_FILE,
            lambda p: p.write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            ),
        )
