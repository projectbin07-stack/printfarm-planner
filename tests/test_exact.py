"""Exact solver: agreement with brute force, and the measured gaps of the heuristics."""

from __future__ import annotations

import itertools

import pytest

from gap_study import report, run
from helpers import MONDAY_8, fleet, job, printer, random_instance
from printfarm_planner.exact import EXACT_PLATE_LIMIT, solve_exact
from printfarm_planner.fleet import Fleet
from printfarm_planner.model import Job
from printfarm_planner.objective import Weights, evaluate
from printfarm_planner.scheduler import Dispatcher, plan, sequences_of, simulate


def brute_force(f: Fleet, jobs: list[Job], weights: Weights) -> float:
    """Lowest objective over every plate-to-printer assignment and order."""
    state = Dispatcher(f, jobs, MONDAY_8, "brute")
    plates = state.plates
    n = len(f.printers)
    best = float("inf")
    for assign in itertools.product(range(n), repeat=len(plates)):
        if any(i not in state.capable[p.job.name] for p, i in zip(plates, assign, strict=True)):
            continue
        groups = [[p for p, i in zip(plates, assign, strict=True) if i == k] for k in range(n)]
        for orders in itertools.product(*(itertools.permutations(g) for g in groups)):
            sequences = {f.printers[k].name: list(orders[k]) for k in range(n)}
            schedule = simulate(f, jobs, MONDAY_8, sequences, calendar=state.schedule.calendar)
            best = min(best, evaluate(schedule, weights).total)
    return best


@pytest.mark.parametrize("seed", range(16))
def test_exact_matches_brute_force(seed: int) -> None:
    f, jobs = random_instance(100 + seed, 3 + seed % 3)
    weights = Weights() if seed % 2 else Weights(makespan=0.2, wait_off=1, material_changes=60)
    solved = solve_exact(f, jobs, MONDAY_8, weights)  # no upper bound: full search
    assert solved.optimal and solved.schedule is not None and solved.objective is not None
    assert solved.objective.total == pytest.approx(brute_force(f, jobs, weights))
    # the returned schedule is what the sequence decoder makes of its own sequences
    again = simulate(f, jobs, MONDAY_8, sequences_of(solved.schedule))
    assert evaluate(again, weights) == solved.objective
    assert solved.schedule.strategy == "exact"


@pytest.mark.parametrize("seed", range(10))
def test_exact_is_never_worse_than_greedy(seed: int) -> None:
    f, jobs = random_instance(200 + seed, 6)
    for strategy in ("fill", "edd"):
        greedy = plan(f, jobs, MONDAY_8, strategy)
        solved = solve_exact(f, jobs, MONDAY_8, upper_bound=greedy)
        assert solved.optimal and solved.objective is not None
        assert solved.objective.total <= evaluate(greedy).total + 1e-9


def test_known_optimum_by_hand() -> None:
    # Two equal printers, one operator, round-the-clock labour, plates of 100 and
    # 60 minutes x2. Setups are serial, so the printer served first should take the two
    # small plates: P1 setup 0-5 prints 5-65, P2 setup 5-10 prints big 10-110, P1 setup
    # 65-70 prints 70-130; P2 is cleared 110-115 and P1 130-135. (Big first on P1 would
    # end the second small plate at 135 and its clearing at 140.)
    f = fleet(printer("P1"), printer("P2"), windows="24/7")
    jobs = [job("big", 100), job("small", 60, copies=2)]
    weights = Weights(tardiness=0, makespan=1, wait_off=0, wait_in=0, material_changes=0)
    solved = solve_exact(f, jobs, MONDAY_8, weights)
    assert solved.objective is not None and solved.objective.makespan == 135
    assert solved.schedule is not None
    assert [(c.printer, c.start, c.end) for c in solved.schedule.clearings] == [
        ("P2", 110, 115),
        ("P1", 130, 135),
    ]


def test_final_wait_is_part_of_the_optimum() -> None:
    # Two printers, one operator, Mon-Fri 08:00-18:00, only waiting is weighted.
    # Long (590 min) on P1, set up 0-5, ends 595 (17:55) and is cleared 595-600 before
    # the window closes; short (60 min) on P2, set up 5-10, ends 70 and is cleared
    # 70-75: no waiting at all. The other way round, long is set up 5-10 and ends at
    # 600 (18:00), too late to clear, and waits 840 min overnight; one printer for both
    # also leaves the second plate waiting overnight. The v0.2.0 objective, which did
    # not count the final plate's wait, scored all of these 0.
    f = fleet(printer("P1"), printer("P2"))
    jobs = [job("long", 590), job("short", 60)]
    weights = Weights(tardiness=0, makespan=0, wait_off=1, wait_in=1, material_changes=0)
    solved = solve_exact(f, jobs, MONDAY_8, weights)
    assert solved.optimal and solved.schedule is not None and solved.objective is not None
    assert solved.objective.total == 0
    assert [(a.printer, a.plate.job.name) for a in solved.schedule.assignments] == [
        ("P1", "long"),
        ("P2", "short"),
    ]
    assert sorted(c.start for c in solved.schedule.clearings) == [70, 595]


def test_limit_and_node_budget() -> None:
    f, jobs = random_instance(7, EXACT_PLATE_LIMIT + 1)
    skipped = solve_exact(f, jobs, MONDAY_8)
    assert skipped.schedule is None and skipped.objective is None and not skipped.optimal
    assert f"above the exact-solver limit of {EXACT_PLATE_LIMIT}" in skipped.note

    f, jobs = random_instance(7, 7)
    greedy = plan(f, jobs, MONDAY_8)
    cut = solve_exact(f, jobs, MONDAY_8, upper_bound=greedy, node_limit=3)
    assert not cut.optimal and cut.nodes == 3 and "node limit" in cut.note
    assert cut.objective is not None and cut.objective.total <= evaluate(greedy).total


def test_nothing_to_schedule() -> None:
    solved = solve_exact(fleet(printer(model="A1")), [job("abs", 60, spools=("ABS",))], MONDAY_8)
    assert solved.optimal and solved.schedule is not None
    assert solved.schedule.assignments == [] and len(solved.schedule.unscheduled) == 1


def test_measured_gaps_on_generated_instances() -> None:
    """Pins what tests/gap_study.py measured (300 search iterations, seeds 0-39).

    These are observations on this generator, not a general claim. The local
    search reached the exact optimum on most instances but not all; on one
    7-plate instance it stayed in a local optimum 78% above it.
    """
    small = run(5, range(40))
    print(report(small))
    assert small.violations == []
    assert (small.optimal("fill"), small.optimal("edd")) == (7, 7)
    assert (small.optimal("hill"), small.optimal("anneal")) == (36, 35)
    assert small.mean_gap("hill") == pytest.approx(0.0054, abs=5e-5)
    assert small.max_gap("hill") == pytest.approx(0.0877, abs=5e-5)
    assert small.unchanged == {"hill": 10, "anneal": 9}

    larger = run(7, range(40))
    print(report(larger))
    assert larger.violations == []
    assert (larger.optimal("fill"), larger.optimal("edd")) == (4, 2)
    assert (larger.optimal("hill"), larger.optimal("anneal")) == (28, 34)
    assert larger.mean_gap("hill") == pytest.approx(0.0328, abs=5e-5)
    assert larger.max_gap("hill") == pytest.approx(0.7814, abs=5e-5)
    assert larger.unchanged == {"hill": 6, "anneal": 5}
