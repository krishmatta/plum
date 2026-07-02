from __future__ import annotations

import abc

from plum import Source


class Method(Source):
    @abc.abstractmethod
    def apply(self, x: int) -> int: ...
