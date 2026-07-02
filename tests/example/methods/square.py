from __future__ import annotations

from tests.example.methods.base import Method
from tests.example.registries import METHODS


@METHODS.register
class Square(Method):
    id = "square"

    def apply(self, x: int) -> int:
        return x * x
