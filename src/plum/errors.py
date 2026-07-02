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
