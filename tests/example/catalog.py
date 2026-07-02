from __future__ import annotations

from plum import Artifact, Catalog, JsonlCodec, JsonModelCodec

from tests.example.schema import Numbers, Power

CATALOG = Catalog()
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))
CATALOG.register(Artifact("powers", JsonlCodec(Power)))
