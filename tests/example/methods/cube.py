from __future__ import annotations

from tests.example.methods.base import Method
from tests.example.registries import METHODS


@METHODS.register
class Cube(Method):
    id = "cube"
    display_name = "Cube (x³)"  # shown by `methods list` in place of the id

    def apply(self, x: int) -> int:
        return x**3
