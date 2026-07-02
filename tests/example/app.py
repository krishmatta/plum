from __future__ import annotations

from plum import autodiscover, build_cli

import tests.example.methods
import tests.example.pipelines
from tests.example.catalog import CATALOG
from tests.example.registries import METHODS, PIPELINES

# imports every module in pipelines/ and methods/, running their registration decorators
autodiscover(tests.example.pipelines)
autodiscover(tests.example.methods)

# sources= adds a `methods list` subcommand
app = build_cli(catalog=CATALOG, pipelines=PIPELINES, sources={"methods": METHODS})

if __name__ == "__main__":
    app()
