from __future__ import annotations

from plum import Registry, Source


class Greeter(Source):
    def greet(self) -> str:
        return f"hello from {self.display_name or self.id}"


GREETERS: Registry[type[Greeter]] = Registry("greeter")
