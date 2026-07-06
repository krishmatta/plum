from __future__ import annotations

from plum import autodiscover, build_cli

import tests.example.experiments
import tests.example.methods
import tests.example.pipelines
from tests.example.catalog import CATALOG
from tests.example.registries import EXPERIMENTS, METHODS, PIPELINES

# imports every module in pipelines/, methods/, and experiments/, running their registrations
autodiscover(tests.example.pipelines)
autodiscover(tests.example.methods)
autodiscover(tests.example.experiments)

# listings= adds `methods list`; experiments= adds `experiments list/run`
app = build_cli(
    catalog=CATALOG,
    pipelines=PIPELINES,
    listings={"methods": METHODS},
    experiments=EXPERIMENTS,
)

if __name__ == "__main__":
    app()
