from __future__ import annotations

import abc

from plum import Source


class Method(Source):
    """The family contract. Source supplies the registry surface: `id` and `display_name`."""

    @abc.abstractmethod
    def apply(self, x: int) -> int: ...
