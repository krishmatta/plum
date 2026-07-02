from __future__ import annotations

from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic

M = TypeVar("M", bound=BaseModel)


class TorchListCodec(Generic[M]):
    """Codec[list[M]]. Reading unpickles (weights_only=False) and can execute code; only read trusted files."""

    extension = ".pth"

    def __init__(self, model: type[M]) -> None:
        self.model = model

    def write(self, objs: list[M], path: Path) -> None:
        import torch

        payload = [o.model_dump() for o in objs]
        write_atomic(path, lambda p: torch.save(payload, p))

    def read(self, path: Path) -> list[M]:
        import torch

        payload = torch.load(path, weights_only=False, map_location="cpu")
        return [self.model.model_validate(x) for x in payload]
