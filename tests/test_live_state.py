"""Live fleets: per-printer starting state and pinned plates (0.6.0)."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence

import pytest

from helpers import MONDAY_8, fleet, job, printer, random_instance
from printfarm_planner.exact import solve_exact
from printfarm_planner.fleet import Fleet
from printfarm_planner.metrics import compute_metrics
from printfarm_planner.model import Job, Plate
from printfarm_planner.objective import Weights, evaluate
from printfarm_planner.scheduler import (
    Dispatcher,
    PrinterState,
    Schedule,
    plan,
    sequences_of,
    simulate,
)
from printfarm_planner.search import SearchConfig, improve

ROUND_THE_CLOCK = {"mon-sun": "00:00-24:00"}


def keys(schedule: Schedule, name: str) -> list[tuple[str, int]]:
    return [(a.plate.job.name, a.plate.copy) for a in schedule.for_printer(name)]


def timeline(schedule: Schedule) -> list[tuple[str, str, int, float, float, float]]:
    return sorted(
        (a.printer, a.plate.job.name, a.plate.copy, a.setup_start, a.start, a.end)
        for a in schedule.assignments
    )


# ------------------------------------------------------------------ defaults


@pytest.mark.parametrize("seed", range(12))
def test_idle_state_and_nothing_pinned_change_nothing(seed: int) -> None:
    f, jobs = random_instance(400 + seed, 6)
    idle = {p.name: PrinterState() for p in f.printers}
    for strategy in ("fill", "edd"):
        before = plan(f, jobs, MONDAY_8, strategy)
        after = plan(f, jobs, MONDAY_8, strategy, initial=idle, pinned={})
        assert timeline(after) == timeline(before)
        assert after.clearings == before.clearings
        assert after.initial == {} and after.pinned == {}
        assert evaluate(after) == evaluate(before)
    config = SearchConfig(iterations=300, seed=seed, anneal=seed % 2 == 1)
    base = plan(f, jobs, MONDAY_8)
    assert timeline(improve(f, jobs, MONDAY_8, base, config=config).schedule) == timeline(
        improve(f, jobs, MONDAY_8, plan(f, jobs, MONDAY_8, initial=idle), config=config).schedule
    )


def test_bad_state_values_are_refused() -> None:
    with pytest.raises(ValueError, match="available_at_min"):
        PrinterState(available_at_min=-1)
    with pytest.raises(ValueError, match="available_at_min"):
        PrinterState(available_at_min=float("nan"))
    f = fleet(printer("A"))
    with pytest.raises(ValueError, match=r"unknown printer.*initial state: B"):
        plan(f, [job()], MONDAY_8, initial={"B": PrinterState()})


# -------------------------------------------------------------- starting state


def test_busy_printer_takes_nothing_before_it_is_free() -> None:
    f = fleet(printer("A"), printer("B"), windows=ROUND_THE_CLOCK, operators=2)
    jobs = [job("x", 60, copies=5)]
    busy = {"A": PrinterState(available_at_min=200, plate_on_bed=True)}
    schedule = plan(f, jobs, MONDAY_8, initial=busy)
    # B starts at once and runs back to back (5 min change + 60 min print); A, still printing
    # until minute 200, takes the plate that is left when it is free.
    assert [(a.setup_start, a.end) for a in schedule.for_printer("B")] == [
        (0, 65),
        (65, 130),
        (130, 195),
        (195, 260),
    ]
    on_a = schedule.for_printer("A")
    assert [(a.ready, a.setup_start, a.end) for a in on_a] == [(200, 200, 265)]


def test_plate_on_bed_is_cleared_even_without_new_plates() -> None:
    f = fleet(printer("A"), printer("B"))  # workday 08:00-18:00, start Monday 08:00
    schedule = plan(f, [job("x", 60)], MONDAY_8, initial={"B": PrinterState(plate_on_bed=True)})
    assert [a.printer for a in schedule.assignments] == ["A"]
    clearing = schedule.clearing_for("B")
    assert clearing is not None
    assert clearing.ready == 0
    # one operator: A's plate change (0-5) comes first in printer order, then B is cleared.
    assert (clearing.start, clearing.end) == (5, 10)


def test_idle_printer_without_a_plate_is_not_cleared() -> None:
    f = fleet(printer("A"), printer("B"))
    schedule = plan(f, [job("x", 60)], MONDAY_8, initial={"B": PrinterState(available_at_min=30)})
    assert schedule.clearing_for("B") is None


def test_waiting_of_a_plate_already_on_the_bed_counts() -> None:
    # A is free at 17:58 (minute 598). A 5 min plate change no longer fits before 18:00, so the
    # next one is at 08:00 the next day (minute 1440), with or without a plate on the bed.
    f = fleet(printer("A"))
    empty = plan(f, [job("x", 180)], MONDAY_8, initial={"A": PrinterState(598)})
    full = plan(f, [job("x", 180)], MONDAY_8, initial={"A": PrinterState(598, plate_on_bed=True)})
    assert [a.setup_start for a in full.assignments] == [1440]
    assert timeline(full) == timeline(empty)
    # Only the plate on the bed waits: 2 min inside labour hours, 840 min overnight.
    before, after = compute_metrics(empty), compute_metrics(full)
    assert after.wait_in_hours_min - before.wait_in_hours_min == pytest.approx(2)
    assert after.wait_off_hours_min - before.wait_off_hours_min == pytest.approx(840)
    assert evaluate(full).total - evaluate(empty).total == pytest.approx(0.5 * 2 + 0.2 * 840)


def test_offline_printer_gets_nothing() -> None:
    f = fleet(printer("A"), printer("B"), windows=ROUND_THE_CLOCK)
    off = {"A": PrinterState(offline=True, plate_on_bed=True)}
    schedule = plan(f, [job("x", 60, copies=3)], MONDAY_8, initial=off)
    assert {a.printer for a in schedule.assignments} == {"B"}
    assert schedule.clearing_for("A") is None
    assert schedule.state_of("A") == PrinterState(offline=True, plate_on_bed=True)


def test_job_only_an_offline_printer_can_run_is_unscheduled_with_the_reason() -> None:
    f = fleet(printer("P", ams=4), printer("A", model="A1"))
    asa = job("asa", 60, spools=("ASA",))  # open-frame A1 is not offered ASA
    schedule = plan(f, [asa, job("pla", 60)], MONDAY_8, initial={"P": PrinterState(offline=True)})
    (missing,) = schedule.unscheduled
    assert missing.job.name == "asa"
    assert "printer is offline" in missing.reasons["P"]
    assert [a.plate.job.name for a in schedule.assignments] == ["pla"]


# --------------------------------------------------------------------- pinned


def pinned_instance() -> tuple[Fleet, list[Job], dict[str, list[Plate]]]:
    f = fleet(printer("A"), printer("B"), windows=ROUND_THE_CLOCK, operators=2)
    late = job("late", 300, due=5000)
    urgent = job("urgent", 60, due=100, priority=2)
    batch = job("batch", 90, copies=3)
    # Both greedy rules would start "urgent" first; A is pinned to run "late" and batch #3.
    return f, [late, urgent, batch], {"A": [Plate(late, 1), Plate(batch, 3)]}


def test_greedy_places_pinned_plates_first_in_order() -> None:
    f, jobs, pinned = pinned_instance()
    for strategy in ("fill", "edd"):
        free = plan(f, jobs, MONDAY_8, strategy)
        assert keys(free, "A")[:1] != [("late", 1)]
        schedule = plan(f, jobs, MONDAY_8, strategy, pinned=pinned)
        assert keys(schedule, "A")[:2] == [("late", 1), ("batch", 3)]
        assert schedule.pinned == {"A": tuple(pinned["A"])}
        placed = sorted((a.plate.job.name, a.plate.copy) for a in schedule.assignments)
        assert placed == sorted(
            [("late", 1), ("urgent", 1), ("batch", 1), ("batch", 2), ("batch", 3)]
        )


@pytest.mark.parametrize(("seed", "anneal"), [(s, a) for s in range(6) for a in (False, True)])
def test_local_search_never_moves_pinned_or_running_work(seed: int, anneal: bool) -> None:
    f, jobs, pinned = pinned_instance()
    start_state = {"A": PrinterState(available_at_min=45, plate_on_bed=True)}
    base = plan(f, jobs, MONDAY_8, "edd", initial=start_state, pinned=pinned)
    weights = Weights(tardiness=50)  # makes moving "late" off A look attractive
    result = improve(
        f, jobs, MONDAY_8, base, weights, SearchConfig(iterations=1500, seed=seed, anneal=anneal)
    )
    schedule = result.schedule
    assert keys(schedule, "A")[:2] == [("late", 1), ("batch", 3)]
    assert all(a.setup_start >= 45 for a in schedule.for_printer("A"))
    assert schedule.pinned == base.pinned and schedule.initial == base.initial
    assert result.objective.total <= result.start_objective.total
    # the search did explore: something other than the pinned plates moved in some runs
    tried = sum(t for t, _ in result.moves.values())
    assert tried > 0


def test_local_search_with_only_pinned_plates_has_no_moves() -> None:
    f = fleet(printer("A"), windows=ROUND_THE_CLOCK)
    a, b = job("a", 60), job("b", 60)
    base = plan(f, [a, b], MONDAY_8, pinned={"A": [Plate(b, 1), Plate(a, 1)]})
    result = improve(f, [a, b], MONDAY_8, base, config=SearchConfig(iterations=50))
    assert result.stopped_by == "no moves"
    assert keys(result.schedule, "A") == [("b", 1), ("a", 1)]


def test_simulate_requires_sequences_to_start_with_the_pinned_plates() -> None:
    f, jobs, pinned = pinned_instance()
    schedule = plan(f, jobs, MONDAY_8, pinned=pinned)
    sequences = sequences_of(schedule)
    again = simulate(f, jobs, MONDAY_8, sequences, pinned=pinned)
    assert timeline(again) == timeline(schedule)
    swapped = dict(sequences)
    swapped["A"] = [sequences["A"][1], sequences["A"][0], *sequences["A"][2:]]
    with pytest.raises(ValueError, match="must start with its pinned plates"):
        simulate(f, jobs, MONDAY_8, swapped, pinned=pinned)


def test_pinned_plates_are_validated() -> None:
    f = fleet(printer("P", ams=4), printer("A", model="A1"))
    pla, asa = job("pla", 60, copies=2), job("asa", 60, spools=("ASA",))
    jobs = [pla, asa]
    cases: list[tuple[Mapping[str, Sequence[Plate]], dict[str, PrinterState], str]] = [
        ({"X": [Plate(pla, 1)]}, {}, "unknown printer 'X'"),
        ({"A": [Plate(pla, 3)]}, {}, "not a schedulable plate"),
        ({"A": [Plate(asa, 1)]}, {}, "cannot run on A"),
        ({"A": [Plate(pla, 1)], "P": [Plate(pla, 1)]}, {}, "pinned twice"),
        ({"A": [Plate(pla, 1)]}, {"A": PrinterState(offline=True)}, "cannot run on A"),
    ]
    for pinned, start_state, message in cases:
        with pytest.raises(ValueError, match=message):
            plan(f, jobs, MONDAY_8, initial=start_state, pinned=pinned)


def test_dispatcher_enforces_pinned_order() -> None:
    f = fleet(printer("A"), printer("B"))
    a, b = job("a", 60), job("b", 60)
    state = Dispatcher(f, [a, b], MONDAY_8, "test", pinned={"A": [Plate(b, 1)]})
    assert state.is_pinned(Plate(b, 1)) and not state.is_pinned(Plate(a, 1))
    assert state.free_plates() == [Plate(a, 1)]
    with pytest.raises(ValueError, match="must run its pinned plate b next"):
        state.place(0, Plate(a, 1))
    with pytest.raises(ValueError, match="b is pinned to A"):
        state.place(1, Plate(b, 1))
    copy = state.copy()
    copy.place(0, Plate(b, 1))
    assert not copy.pinned[0] and state.pinned[0]  # the copy's queue is its own
    state.place(0, Plate(b, 1))
    state.place(0, Plate(a, 1))
    state.clear(0)


def test_clearing_is_refused_while_pinned_plates_are_left() -> None:
    f = fleet(printer("A"))
    a = job("a", 60)
    state = Dispatcher(
        f,
        [a],
        MONDAY_8,
        "test",
        initial={"A": PrinterState(plate_on_bed=True)},
        pinned={"A": [Plate(a, 1)]},
    )
    with pytest.raises(ValueError, match="still has pinned plates"):
        state.clear(0)


# --------------------------------------------------------------- exact solver


def brute_force(
    f: Fleet,
    jobs: list[Job],
    weights: Weights,
    initial: Mapping[str, PrinterState],
    pinned: Mapping[str, Sequence[Plate]],
) -> float:
    """Lowest objective over every order of the free plates after each printer's pinned ones."""
    state = Dispatcher(f, jobs, MONDAY_8, "brute", initial=initial, pinned=pinned)
    plates = state.free_plates()
    n = len(f.printers)
    best = float("inf")
    for assign in itertools.product(range(n), repeat=len(plates)):
        if any(i not in state.capable[p.job.name] for p, i in zip(plates, assign, strict=True)):
            continue
        groups = [[p for p, i in zip(plates, assign, strict=True) if i == k] for k in range(n)]
        for orders in itertools.product(*(itertools.permutations(g) for g in groups)):
            sequences = {
                f.printers[k].name: [*pinned.get(f.printers[k].name, ()), *orders[k]]
                for k in range(n)
            }
            schedule = simulate(
                f,
                jobs,
                MONDAY_8,
                sequences,
                initial=initial,
                pinned=pinned,
                calendar=state.schedule.calendar,
            )
            best = min(best, evaluate(schedule, weights).total)
    return best


@pytest.mark.parametrize("seed", range(10))
def test_exact_with_live_state_matches_brute_force(seed: int) -> None:
    f, jobs = random_instance(700 + seed, 4 + seed % 2)
    names = [p.name for p in f.printers]
    start_state = {
        names[0]: PrinterState(available_at_min=[0, 90, 400][seed % 3], plate_on_bed=seed % 2 == 0)
    }
    if len(names) > 2 and seed % 4 == 0:
        start_state[names[2]] = PrinterState(offline=True)
    probe = Dispatcher(f, jobs, MONDAY_8, "probe", initial=start_state)
    first = probe.plates[0]
    target = min(probe.capable[first.job.name])
    pinned = {names[target]: [first]}
    weights = Weights() if seed % 2 else Weights(makespan=0.2, wait_off=1, material_changes=60)
    solved = solve_exact(f, jobs, MONDAY_8, weights, initial=start_state, pinned=pinned)
    assert solved.optimal and solved.schedule is not None and solved.objective is not None
    expected = brute_force(f, jobs, weights, start_state, pinned)
    assert solved.objective.total == pytest.approx(expected)
    assert keys(solved.schedule, names[target])[0] == (first.job.name, first.copy)
    busy = start_state[names[0]].available_at_min
    assert all(a.setup_start >= busy for a in solved.schedule.for_printer(names[0]))
    # its own sequences, timed again with the same live state, give the same objective
    again = simulate(
        f, jobs, MONDAY_8, sequences_of(solved.schedule), initial=start_state, pinned=pinned
    )
    assert evaluate(again, weights) == solved.objective
    # seeded with the greedy plan it reaches the same optimum
    greedy = plan(f, jobs, MONDAY_8, initial=start_state, pinned=pinned)
    seeded = solve_exact(
        f, jobs, MONDAY_8, weights, upper_bound=greedy, initial=start_state, pinned=pinned
    )
    assert seeded.objective is not None
    assert seeded.objective.total == pytest.approx(expected)


def test_compare_passes_the_live_state_to_every_planner() -> None:
    from printfarm_planner.compare import compare

    f, jobs, pinned = pinned_instance()
    start_state = {"B": PrinterState(available_at_min=30, plate_on_bed=True)}
    runs = compare(
        f, jobs, MONDAY_8, config=SearchConfig(iterations=200), initial=start_state, pinned=pinned
    )
    assert [r.method for r in runs] == ["fill", "edd", "hill", "anneal", "exact"]
    for run in runs:
        assert run.schedule is not None, run.method
        assert keys(run.schedule, "A")[:2] == [("late", 1), ("batch", 3)], run.method
        assert all(a.setup_start >= 30 for a in run.schedule.for_printer("B")), run.method
