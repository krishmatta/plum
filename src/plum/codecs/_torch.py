from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

from plum.codecs._atomic import write_atomic

M = TypeVar("M", bound=BaseModel)


class TorchListCodec(Generic[M]):
    extension = ".pth"

    def __init__(
        self,
        model: type[M],
        *,
        to_obj: Callable[[M], Any] | None = None,
        from_obj: Callable[[Any], M] | None = None,
    ) -> None:
        self.model = model
        self._to_obj = to_obj or (lambda m: m.model_dump())
        self._from_obj = from_obj or (lambda x: model.model_validate(x))

    def write(self, objs: list[M], path: Path) -> None:
        import torch

        payload = [self._to_obj(o) for o in objs]
        write_atomic(path, lambda p: torch.save(payload, p))

    def read(self, path: Path) -> list[M]:
        import torch

        payload = torch.load(path, weights_only=False, map_location="cpu")
        return [self._from_obj(x) for x in payload]
