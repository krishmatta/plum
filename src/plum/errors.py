from __future__ import annotations


class PlumError(Exception):
    pass


class DuplicateRegistration(PlumError):
    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} '{name}' is already registered")


class UnknownName(PlumError):
    def __init__(self, kind: str, name: str, known: list[str]) -> None:
        self.kind = kind
        self.name = name
        self.known = list(known)
        listed = ", ".join(sorted(self.known)) or "(none)"
        super().__init__(f"unknown {kind} '{name}'; known: {listed}")


class UnknownArtifact(UnknownName):
    def __init__(self, name: str, known: list[str]) -> None:
        super().__init__("artifact", name, known)


class ParamsMismatch(PlumError):
    def __init__(
        self, pipeline: str, run_id: str, stored: dict, requested: dict
    ) -> None:
        self.pipeline = pipeline
        self.run_id = run_id
        self.stored = stored
        self.requested = requested
        missing = object()

        def show(v: object) -> str:
            return "(absent)" if v is missing else repr(v)

        diffs = [
            f"{k}: stored={show(stored.get(k, missing))} requested={show(requested.get(k, missing))}"
            for k in sorted(set(stored) | set(requested))
            if stored.get(k, missing) != requested.get(k, missing)
        ]
        super().__init__(
            f"{pipeline} run '{run_id}' was computed with different params ("
            + "; ".join(diffs)
            + "); pass force=True to start over"
        )


class PriorRunFailed(PlumError):
    def __init__(self, pipeline: str, run_id: str, error: str | None) -> None:
        self.pipeline = pipeline
        self.run_id = run_id
        self.error = error
        cause = f": {error.strip().splitlines()[-1]}" if error else ""
        super().__init__(
            f"{pipeline} run '{run_id}' previously failed{cause}; "
            "pass resume=True to continue from checkpoints or force=True to start over"
        )


class SyncConflict(PlumError):
    def __init__(self, direction: str, runs: list[str]) -> None:
        self.direction = direction
        self.runs = sorted(runs)
        super().__init__(
            f"{direction} blocked: {len(self.runs)} run(s) differ between local and "
            f"remote (" + "; ".join(self.runs) + "); pass --force to overwrite"
        )


class DirtyWorkingTree(PlumError):
    def __init__(self, repo: str, reason: str) -> None:
        self.repo = repo
        self.reason = reason
        super().__init__(
            f"refusing to run: {repo} {reason}; commit (or gitignore) so this run "
            "maps to a clean commit"
        )
