from __future__ import annotations

from tests.example.registries import GREETERS, Greeter


@GREETERS.register
class English(Greeter):
    id = "english"
    display_name = "English"
