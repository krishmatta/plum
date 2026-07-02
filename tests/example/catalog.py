from __future__ import annotations

from plum import Artifact, Catalog, JsonlCodec, JsonModelCodec

from tests.example.schema import Numbers, Power

# Every on-disk output is declared here once. Pipelines, the store, and the
# CLI resolve artifact names through this.
CATALOG = Catalog()
# one Numbers per numbers.json
CATALOG.register(Artifact("numbers", JsonModelCodec(Numbers)))
# a list of Power, one per line of powers.jsonl
CATALOG.register(Artifact("powers", JsonlCodec(Power)))
