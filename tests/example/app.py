from __future__ import annotations

from plum import autodiscover, build_cli

import tests.example.methods
import tests.example.pipelines  # noqa: F401 — registers the pipelines
from tests.example.catalog import CATALOG
from tests.example.registries import METHODS, PIPELINES

autodiscover(tests.example.methods)

app = build_cli(catalog=CATALOG, pipelines=PIPELINES, sources={"methods": METHODS})
