from __future__ import annotations

from plum import Experiment, sweep

from tests.example.registries import EXPERIMENTS


@EXPERIMENTS.register
class MethodSweep(Experiment):
    id = "method-sweep"

    def run(self, runner):
        # the reusable upstream, computed once and shared by every combo
        runner.run("load", "base", n=6, description="shared numbers")
        for params, run_id in sweep(
            {"method": ["square", "cube"]},
            run_id=lambda p: f"pow-{p['method']}",
        ):
            runner.run(
                "apply",
                run_id,
                numbers_run="base",
                method=params["method"],
                description=f"powers via {params['method']}",
            )
