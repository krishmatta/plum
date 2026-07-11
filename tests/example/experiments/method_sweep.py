from __future__ import annotations

from plum import Experiment, sweep

from tests.example.registries import EXPERIMENTS


@EXPERIMENTS.register
class MethodSweep(Experiment):
    id = "method-sweep"

    class Params(Experiment.Params):
        n: int = 6

    def _run(self, runner, params):
        # run ids derive from params, so two invocations with different n get
        # their own upstream instead of colliding under ParamsMismatch
        base = f"base-n{params.n}"
        # the reusable upstream, computed once and shared by every combo
        runner.run("load", base, n=params.n, description="shared numbers")
        for combo, run_id in sweep(
            {"method": ["square", "cube"]},
            run_id=lambda p: f"pow-{p['method']}-n{params.n}",
        ):
            runner.run(
                "apply",
                run_id,
                numbers_run=base,
                method=combo["method"],
                description=f"powers via {combo['method']}",
            )
