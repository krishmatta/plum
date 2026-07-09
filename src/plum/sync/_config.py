from __future__ import annotations

from pathlib import Path

from plum.errors import PlumError, UnknownName
from plum.sync._backend import BACKENDS, SyncBackend


def load_remote(name: str = "origin", *, config_path: Path | str = "plum.toml") -> SyncBackend:
    """Resolve a remote from plum.toml. Every key but `backend` is a field of the
    chosen backend's Options and is pydantic-validated."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise PlumError(
            f"no {config_path} in {Path.cwd()}; declare a [remotes.<name>] table "
            "with a backend id and its options"
        )
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    remotes = data.get("remotes", {})
    if name not in remotes:
        raise UnknownName("remote", name, list(remotes))
    section = dict(remotes[name])
    backend_id = section.pop("backend", None)
    if backend_id is None:
        raise PlumError(f"remote '{name}' has no 'backend' key")
    backend_cls = BACKENDS.get(backend_id)
    return backend_cls(backend_cls.Options(**section))
