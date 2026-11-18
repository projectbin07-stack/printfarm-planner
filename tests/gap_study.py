"""Optimality-gap study on generated instances (used by the tests; also runnable).

    python tests/gap_study.py

For each seed it builds :func:`helpers.random_instance`, runs ``fill``, ``edd``,
hill climbing and annealing (both starting from the better greedy schedule) and,
when the instance is small enough, the exact solver. The gap of a method is
``(J_method - J_best) / J_best`` where ``J_best`` is the exact optimum (or, for
larger instances, the lowest objective any method found).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import MONDAY_8, random_instance
from printfarm_planner.exact import solve_exact
from printfarm_planner.objective import evaluate
from printfarm_planner.scheduler import plan
from printfarm_planner.search import SearchConfig, improve

METHODS = ("fill", "edd", "hill", "anneal")


@dataclass
class Study:
    plates: int
    instances: int = 0
    exact: bool = False
    exact_seconds_max: float = 0.0
    #: method -> gap per instance (fraction)
    gaps: dict[str, list[float]] = field(default_factory=lambda: {m: [] for m in METHODS})
    #: instances where the local search did not improve on its start, per method
    unchanged: dict[str, int] = field(default_factory=lambda: {"hill": 0, "anneal": 0})
    #: an exact or method objective that was worse than a heuristic (must stay empty)
    violations: list[str] = field(default_factory=list)

    def optimal(self, method: str) -> int:
        return sum(1 for g in self.gaps[method] if g < 1e-9)

    def mean_gap(self, method: str) -> float:
        values = self.gaps[method]
        return sum(values) / len(values) if values else 0.0

    def max_gap(self, method: str) -> float:
        return max(self.gaps[method], default=0.0)


def run(plates: int, seeds: range, iterations: int = 300, exact: bool = True) -> Study:
    study = Study(plates=plates, exact=exact)
    for seed in seeds:
        fleet, jobs = random_instance(seed, plates)
        totals: dict[str, float] = {}
        greedy = {s: plan(fleet, jobs, MONDAY_8, s) for s in ("fill", "edd")}
        for name, schedule in greedy.items():
            totals[name] = evaluate(schedule).total
        base_name = min(greedy, key=lambda s: totals[s])
        for anneal in (False, True):
            config = SearchConfig(iterations=iterations, seed=seed, anneal=anneal)
            result = improve(fleet, jobs, MONDAY_8, greedy[base_name], config=config)
            name = "anneal" if anneal else "hill"
            totals[name] = result.objective.total
            if result.objective.total > totals[base_name] + 1e-9:
                study.violations.append(f"seed {seed}: {name} worse than its start")
            if not result.objective.better_than(result.start_objective):
                study.unchanged[name] += 1
        if exact:
            clock = time.perf_counter()
            solved = solve_exact(fleet, jobs, MONDAY_8, upper_bound=greedy[base_name])
            study.exact_seconds_max = max(study.exact_seconds_max, time.perf_counter() - clock)
            assert solved.objective is not None
            best = solved.objective.total
            if not solved.optimal:
                study.violations.append(f"seed {seed}: exact search hit its node limit")
            for name, value in totals.items():
                if value < best - 1e-6:
                    study.violations.append(f"seed {seed}: {name} beat the exact solver")
        else:
            best = min(totals.values())
        for name in METHODS:
            study.gaps[name].append((totals[name] - best) / best if best > 0 else 0.0)
        study.instances += 1
    return study


def report(study: Study) -> str:
    reference = "exact optimum" if study.exact else "best method"
    lines = [
        f"{study.instances} instances of {study.plates} plates, gap vs {reference}"
        + (f" (exact solver max {study.exact_seconds_max:.2f} s)" if study.exact else "")
    ]
    for name in METHODS:
        line = (
            f"  {name:7} at best {study.optimal(name):3}/{study.instances}"
            f"  mean gap {study.mean_gap(name) * 100:6.2f}%"
            f"  max gap {study.max_gap(name) * 100:7.2f}%"
        )
        if name in study.unchanged:
            line += f"  no improvement on {study.unchanged[name]}"
        lines.append(line)
    if study.violations:
        lines.append("  VIOLATIONS: " + "; ".join(study.violations))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(run(5, range(40))))
    print(report(run(7, range(40))))
    print(report(run(20, range(20), iterations=2000, exact=False)))
