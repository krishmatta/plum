from __future__ import annotations

from plum import autodiscover, build_cli

import tests.example.methods
import tests.example.pipelines  # noqa: F401; importing runs the @PIPELINES.register decorators
from tests.example.catalog import CATALOG
from tests.example.registries import METHODS, PIPELINES

# imports every module in methods/, which registers the sources
autodiscover(tests.example.methods)

# sources= adds a `methods list` subcommand
app = build_cli(catalog=CATALOG, pipelines=PIPELINES, sources={"methods": METHODS})

if __name__ == "__main__":
    app()
