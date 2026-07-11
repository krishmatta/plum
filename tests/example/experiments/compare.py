from __future__ import annotations

from plum import Experiment

from tests.example.registries import EXPERIMENTS


@EXPERIMENTS.register
class Compare(Experiment):
    id = "compare"

    def _run(self, runner, params):
        # composition: each child invocation's own run, plus everything it ensured,
        # becomes an input of this invocation, so a lineage pull fetches it all
        for n in (3, 6):
            runner.invoke("method-sweep", f"sweep-n{n}", n=n)
