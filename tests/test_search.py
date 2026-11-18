"""Local-search improvement phase."""

from __future__ import annotations

import itertools

import pytest

from helpers import MONDAY_8, fleet, job, printer, random_instance
from printfarm_planner.compat import incompatibilities
from printfarm_planner.objective import Weights, evaluate
from printfarm_planner.scheduler import Schedule, plan, sequences_of, simulate
from printfarm_planner.search import NEIGHBOURHOODS, SearchConfig, improve


def assert_valid(schedule: Schedule) -> None:
    """Every plate once, compatible printer, no overlaps, operator tasks inside windows,
    and every used printer's final plate cleared once, after it finished."""
    expected = sorted((j.name, c) for j in schedule.jobs for c in range(1, j.copies + 1))
    got = sorted((a.plate.job.name, a.plate.copy) for a in schedule.assignments)
    assert got == expected
    by_name = {p.name: p for p in schedule.printers}
    for a in schedule.assignments:
        assert not incompatibilities(by_name[a.printer], a.plate.job)
        lo, hi = schedule.calendar.next_window(a.setup_start)
        assert lo <= a.setup_start and a.start <= hi
    for p in schedule.printers:
        for prev, cur in itertools.pairwise(schedule.for_printer(p.name)):
            assert cur.setup_start >= prev.end
    used = sorted({a.printer for a in schedule.assignments})
    assert sorted(c.printer for c in schedule.clearings) == used
    for c in schedule.clearings:
        assert c.start >= max(a.end for a in schedule.for_printer(c.printer)) == c.ready
        lo, hi = schedule.calendar.next_window(c.start)
        assert lo <= c.start and c.end <= hi
    for operator in range(schedule.labour.operators):
        tasks = sorted(
            [(a.setup_start, a.start) for a in schedule.assignments if a.operator == operator]
            + [(c.start, c.end) for c in schedule.clearings if c.operator == operator]
        )
        for (_, end), (begin, _) in itertools.pairwise(tasks):
            assert begin >= end


@pytest.mark.parametrize("anneal", [False, True])
@pytest.mark.parametrize("seed", range(12))
def test_never_worse_than_start_and_valid(seed: int, anneal: bool) -> None:
    f, jobs = random_instance(seed, 9)
    for strategy in ("fill", "edd"):
        start = plan(f, jobs, MONDAY_8, strategy)
        config = SearchConfig(iterations=150, seed=seed, anneal=anneal)
        result = improve(f, jobs, MONDAY_8, start, config=config)
        assert result.objective.total <= evaluate(start).total + 1e-9
        assert result.start_objective == evaluate(start)
        assert result.objective == evaluate(result.schedule)
        assert_valid(result.schedule)
        assert result.schedule.strategy == f"{strategy}+{config.rule}"


def test_annealing_accepts_worse_moves_but_returns_the_best() -> None:
    f, jobs = random_instance(3, 9)
    start = plan(f, jobs, MONDAY_8)
    # a very hot search that never cools accepts nearly every applicable move
    config = SearchConfig(iterations=200, seed=1, anneal=True, t0=1e9, t_end_ratio=1.0)
    result = improve(f, jobs, MONDAY_8, start, config=config)
    tried = sum(t for t, _ in result.moves.values())
    assert result.accepted == tried > 0
    assert result.objective.total <= result.start_objective.total
    assert result.objective == evaluate(result.schedule)


def test_deterministic_for_a_seed() -> None:
    f, jobs = random_instance(5, 10)
    start = plan(f, jobs, MONDAY_8, "edd")
    for anneal in (False, True):
        config = SearchConfig(iterations=200, seed=42, anneal=anneal)
        first = improve(f, jobs, MONDAY_8, start, config=config)
        second = improve(f, jobs, MONDAY_8, start, config=config)
        assert first.schedule.assignments == second.schedule.assignments
        assert (first.accepted, first.improvements, first.moves) == (
            second.accepted,
            second.improvements,
            second.moves,
        )


def test_iteration_budget_is_respected() -> None:
    f, jobs = random_instance(2, 8)
    start = plan(f, jobs, MONDAY_8)
    result = improve(f, jobs, MONDAY_8, start, config=SearchConfig(iterations=37))
    assert result.iterations == 37
    assert result.stopped_by == "iterations"
    assert sum(tried for tried, _ in result.moves.values()) <= 37
    assert set(result.moves) == set(NEIGHBOURHOODS)
    zero = improve(f, jobs, MONDAY_8, start, config=SearchConfig(iterations=0))
    assert zero.iterations == 0 and zero.schedule.assignments == start.assignments


def test_time_limit_stops_the_search() -> None:
    f, jobs = random_instance(2, 12)
    start = plan(f, jobs, MONDAY_8)
    config = SearchConfig(iterations=10**9, time_limit_s=0.05)
    result = improve(f, jobs, MONDAY_8, start, config=config)
    assert result.stopped_by == "time"
    assert result.seconds < 5
    assert result.objective.total <= result.start_objective.total


def test_single_plate_has_no_moves() -> None:
    f = fleet(printer())
    jobs = [job("only", 60)]
    start = plan(f, jobs, MONDAY_8)
    result = improve(f, jobs, MONDAY_8, start)
    assert result.stopped_by == "no moves"
    assert result.iterations == 0
    assert result.schedule.assignments == start.assignments


def test_finds_the_better_order_from_edd() -> None:
    # edd (no due dates) runs the 10 h plate first: it ends 18:05, waits all night
    # (835 min off hours), B runs on Tuesday and the last B is cleared 1815-1820:
    # J = 1820 + 0.2 x 835 = 1987. Running B first, as in
    # test_scheduler.test_fill_runs_short_plates_first_and_long_one_overnight, ends A
    # at 980 (Tue 00:20); A waits until Tue 08:00 and is cleared 1440-1445:
    # J = 1445 + 0.2 x 460 = 1537. (B, B, A, B gives 1570 + 0.2 x 585 = 1687.)
    f = fleet(printer())
    jobs = [job("A", 600), job("B", 120, copies=3)]
    start = plan(f, jobs, MONDAY_8, "edd")
    assert evaluate(start).makespan == 1820
    assert evaluate(start).total == pytest.approx(1987)
    result = improve(f, jobs, MONDAY_8, start, config=SearchConfig(iterations=200, seed=0))
    assert result.objective.makespan == 1445
    assert result.objective.wait_off == 460
    assert result.objective.total == pytest.approx(1537)
    assert [p.label for p in sequences_of(result.schedule)["P1"]][-1] == "A"


def test_reassign_balances_printers() -> None:
    # One printer holds both plates of the long job; moving one to P2 shortens the plan.
    f = fleet(printer("P1"), printer("P2"), windows="24/7")
    jobs = [job("long", 600, copies=2), job("short", 60, copies=2)]
    weights = Weights(tardiness=0, makespan=1, wait_off=0, wait_in=0, material_changes=0)

    plates = sequences_of(plan(f, jobs, MONDAY_8))
    everything = [p for seq in plates.values() for p in seq]
    longs = [p for p in everything if p.job.name == "long"]
    shorts = [p for p in everything if p.job.name == "short"]
    lopsided = simulate(f, jobs, MONDAY_8, {"P1": longs, "P2": shorts})
    # 5 + 600 + 5 + 600, then P1 is cleared 1210-1215
    assert evaluate(lopsided, weights).makespan == 1215
    result = improve(f, jobs, MONDAY_8, lopsided, weights, SearchConfig(iterations=200))
    assert result.objective.makespan < 1215
    assert {a.printer for a in result.schedule.assignments if a.plate.job.name == "long"} == {
        "P1",
        "P2",
    }
    assert result.moves["reassign"][1] + result.moves["swap"][1] > 0


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="iterations"):
        SearchConfig(iterations=-1)
    with pytest.raises(ValueError, match="time limit"):
        SearchConfig(time_limit_s=0)
    with pytest.raises(ValueError, match="t0"):
        SearchConfig(t0=0)
    with pytest.raises(ValueError, match="t_end_ratio"):
        SearchConfig(t_end_ratio=0)
    assert SearchConfig(anneal=True).rule == "anneal"
