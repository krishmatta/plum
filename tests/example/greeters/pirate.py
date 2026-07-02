from __future__ import annotations

from tests.example.registries import GREETERS, Greeter


@GREETERS.register
class Pirate(Greeter):
    id = "pirate"
