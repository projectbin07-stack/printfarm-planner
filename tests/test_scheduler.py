"""Scheduler scenarios with hand-derived timelines.

All plans start Monday 08:00 with a Mon-Fri 08:00-18:00 labour window unless
stated, so minute 600 is Monday 18:00 and minute 1440 is Tuesday 08:00.
"""

from __future__ import annotations

import itertools

import pytest

from helpers import MONDAY_8, fleet, job, printer
from printfarm_planner.metrics import compute_metrics
from printfarm_planner.model import Job, Plate
from printfarm_planner.scheduler import Assignment, Dispatcher, plan, sequences_of, simulate


def times(assignments: list[Assignment]) -> list[tuple[str, float, float, float]]:
    return [(a.plate.label, a.setup_start, a.start, a.end) for a in assignments]


def test_overnight_print_waits_for_morning() -> None:
    schedule = plan(fleet(printer()), [job("long", 720, copies=2)], MONDAY_8)
    # plate 1: setup 0-5, print 5-725 (Mon 20:05); plate 2 setup Tue 08:00
    assert times(schedule.for_printer("P1")) == [
        ("long #1", 0, 5, 725),
        ("long #2", 1440, 1445, 2165),
    ]
    metrics = compute_metrics(schedule)
    assert metrics.makespan_min == 2165
    assert metrics.printers[0].wait_off_hours_min == pytest.approx(1440 - 725)
    assert metrics.printers[0].wait_in_hours_min == 0
    assert metrics.fleet_utilisation == pytest.approx(1440 / 2165)


def test_setup_must_fit_before_window_closes() -> None:
    # 593 min print ends 17:58; a 5 minute change no longer fits that day.
    schedule = plan(fleet(printer()), [job("j", 593, copies=2)], MONDAY_8)
    assert [a.setup_start for a in schedule.assignments] == [0, 1440]
    metrics = compute_metrics(schedule)
    # 17:58-18:00 is inside the window, the rest of the night is not
    assert metrics.wait_in_hours_min == pytest.approx(2)
    assert metrics.wait_off_hours_min == pytest.approx(1440 - 600)


def test_fill_runs_short_plates_first_and_long_one_overnight() -> None:
    jobs = [job("A", 600), job("B", 120, copies=3)]
    schedule = plan(fleet(printer()), jobs, MONDAY_8, strategy="fill")
    # At 0: A would end 18:05 and wait all night; B ends 10:05 and waits 0.
    # B, B, B back to back, then A starts 14:20 and ends 00:20 with nothing after it.
    assert times(schedule.assignments) == [
        ("B #1", 0, 5, 125),
        ("B #2", 125, 130, 250),
        ("B #3", 250, 255, 375),
        ("A", 375, 380, 980),
    ]


def test_edd_without_due_dates_runs_longest_first() -> None:
    jobs = [job("A", 600), job("B", 120, copies=3)]
    schedule = plan(fleet(printer()), jobs, MONDAY_8, strategy="edd")
    assert times(schedule.assignments) == [
        ("A", 0, 5, 605),
        ("B #1", 1440, 1445, 1565),
        ("B #2", 1565, 1570, 1690),
        ("B #3", 1690, 1695, 1815),
    ]


def test_edd_honours_due_dates() -> None:
    jobs = [job("late-due", 60, due=2000), job("soon", 30, due=100)]
    schedule = plan(fleet(printer()), jobs, MONDAY_8, strategy="edd")
    assert [a.plate.job.name for a in schedule.assignments] == ["soon", "late-due"]
    metrics = compute_metrics(schedule)
    assert metrics.late_jobs == 0
    soon = next(j for j in metrics.jobs if j.name == "soon")
    assert soon.finish_min == 35


def test_lateness_is_reported() -> None:
    schedule = plan(fleet(printer()), [job("j", 120, due=60)], MONDAY_8)
    metrics = compute_metrics(schedule)
    assert metrics.jobs[0].late_min == 125 - 60
    assert metrics.late_jobs == 1


def test_priority_beats_waiting_time() -> None:
    jobs = [job("short", 60), job("urgent", 600, priority=5)]
    schedule = plan(fleet(printer()), jobs, MONDAY_8)
    assert schedule.assignments[0].plate.job.name == "urgent"


def test_single_operator_staggers_starts() -> None:
    f = fleet(printer("P1"), printer("P2"))
    schedule = plan(f, [job("j", 60, copies=2)], MONDAY_8)
    assert sorted((a.printer, a.setup_start, a.start) for a in schedule.assignments) == [
        ("P1", 0, 5),
        ("P2", 5, 10),
    ]


def test_two_operators_start_together() -> None:
    f = fleet(printer("P1"), printer("P2"), operators=2)
    schedule = plan(f, [job("j", 60, copies=2)], MONDAY_8)
    assert sorted((a.printer, a.start, a.operator) for a in schedule.assignments) == [
        ("P1", 5, 0),
        ("P2", 5, 1),
    ]


def test_material_change_adds_swap_time() -> None:
    p = printer(loaded=("PLA",))
    jobs = [job("petg", 60, spools=("PETG",), priority=1), job("pla", 60, spools=("PLA",))]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    # PETG: change 5 + swap 10 -> print 15-75; PLA: swap back, setup 75-90, print 90-150
    assert times(schedule.assignments) == [("petg", 0, 15, 75), ("pla", 75, 90, 150)]
    assert [a.material_change for a in schedule.assignments] == [True, True]
    assert compute_metrics(schedule).material_changes == 2


def test_ams_keeps_previous_spools_loaded() -> None:
    p = printer(ams=4, loaded=("PLA:black",))
    jobs = [
        job("white", 60, spools=("PLA:white",), priority=2),
        job("black", 60, spools=("PLA:black",), priority=1),
    ]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    assert [a.material_change for a in schedule.assignments] == [True, False]
    assert schedule.assignments[0].loaded == ("PLA:white", "PLA:black")


def test_external_spool_holds_only_the_last_material() -> None:
    p = printer(ams=0)
    jobs = [
        job("a", 60, spools=("PLA:red",), priority=3),
        job("b", 60, spools=("PLA:blue",), priority=2),
        job("c", 60, spools=("PLA:red",), priority=1),
    ]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    # nothing declared loaded: the first plate needs no swap, then every colour change does
    assert [a.material_change for a in schedule.assignments] == [False, True, True]
    assert [a.loaded for a in schedule.assignments] == [("PLA:red",), ("PLA:blue",), ("PLA:red",)]


def test_fill_prefers_no_material_change_on_tie() -> None:
    p = printer(loaded=("PLA",))
    jobs = [job("petg", 60, spools=("PETG",)), job("pla", 60, spools=("PLA",))]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    assert schedule.assignments[0].plate.job.name == "pla"


def test_time_factor_scales_print_time() -> None:
    schedule = plan(fleet(printer(time_factor=1.5)), [job("j", 60)], MONDAY_8)
    assert schedule.assignments[0].print_min == pytest.approx(90)


def test_job_goes_to_the_only_capable_printer() -> None:
    f = fleet(printer("A1", model="A1"), printer("P1S", model="P1S"))
    schedule = plan(f, [job("asa", 60, spools=("ASA",), copies=2)], MONDAY_8)
    assert {a.printer for a in schedule.assignments} == {"P1S"}


def test_restricted_plates_go_to_their_printer_first() -> None:
    # Both printers can do "any"; only P1S can do "asa". When P1S is picked first it
    # should take the plate nobody else can print.
    f = fleet(printer("P1S", model="P1S"), printer("A1", model="A1"))
    jobs = [job("any", 60), job("asa", 60, spools=("ASA",))]
    schedule = plan(f, jobs, MONDAY_8)
    first = schedule.for_printer("P1S")[0]
    assert first.plate.job.name == "asa"


def test_unschedulable_job_is_reported_with_reasons() -> None:
    f = fleet(printer("A1", model="A1"))
    schedule = plan(f, [job("ok", 60), job("abs", 60, spools=("ABS",))], MONDAY_8)
    assert len(schedule.assignments) == 1
    assert [u.job.name for u in schedule.unscheduled] == ["abs"]
    assert schedule.unscheduled[0].reasons == {"A1": ["material ABS not allowed on this printer"]}
    assert compute_metrics(schedule).unscheduled_jobs == 1


def test_nothing_schedulable_gives_empty_metrics() -> None:
    schedule = plan(fleet(printer(model="A1")), [job("abs", 60, spools=("ABS",))], MONDAY_8)
    metrics = compute_metrics(schedule)
    assert metrics.makespan_min == 0
    assert metrics.fleet_utilisation == 0
    assert metrics.printers[0].utilisation == 0


def test_round_the_clock_labour_has_no_waiting() -> None:
    schedule = plan(fleet(printer(), windows="24/7"), [job("j", 720, copies=2)], MONDAY_8)
    assert times(schedule.assignments) == [("j #1", 0, 5, 725), ("j #2", 725, 730, 1450)]
    metrics = compute_metrics(schedule)
    assert metrics.wait_off_hours_min == 0


def test_mass_total_only_when_every_job_has_one() -> None:
    sched = plan(fleet(printer()), [job("a", 60, mass=10.0, copies=2)], MONDAY_8)
    assert compute_metrics(sched).mass_g == 20.0
    sched = plan(fleet(printer()), [job("a", 60, mass=10.0), job("b", 60)], MONDAY_8)
    assert compute_metrics(sched).mass_g is None


def test_bad_strategy_and_duplicate_names() -> None:
    with pytest.raises(ValueError, match="strategy"):
        plan(fleet(printer()), [job()], MONDAY_8, strategy="random")
    with pytest.raises(ValueError, match="unique"):
        plan(fleet(printer()), [job("x"), job("x")], MONDAY_8)


def test_every_plate_scheduled_once_and_no_overlaps() -> None:
    f = fleet(
        printer("P1", ams=4), printer("P2", model="A1", ams=4), printer("P3", model="A1 Mini")
    )
    jobs = [
        job("a", 95, copies=5),
        job("b", 400, copies=3, spools=("PETG",)),
        job("c", 30, copies=7, spools=("PLA:red",)),
        job("d", 1000, copies=1),
    ]
    schedule = plan(f, jobs, MONDAY_8)
    labels = [a.plate.label for a in schedule.assignments]
    assert len(labels) == len(set(labels)) == 16
    for p in f.printers:
        rows = schedule.for_printer(p.name)
        for prev, cur in itertools.pairwise(rows):
            assert cur.setup_start >= prev.end
    ops = sorted((a.setup_start, a.start) for a in schedule.assignments)
    for (_, e1), (s2, _) in itertools.pairwise(ops):
        assert s2 >= e1  # one operator: tasks never overlap
    cal = schedule.calendar
    for a in schedule.assignments:
        lo, hi = cal.next_window(a.setup_start)
        assert lo <= a.setup_start and a.start <= hi


def _example_like_jobs() -> list[Job]:
    return [
        job("a", 95, copies=5, due=600),
        job("b", 400, copies=3, spools=("PETG",)),
        job("c", 30, copies=7, spools=("PLA:red",), priority=1),
        job("d", 1000, copies=1, due=900),
        job("asa", 200, copies=2, spools=("ASA",)),
    ]


@pytest.mark.parametrize("strategy", ["fill", "edd"])
@pytest.mark.parametrize("operators", [1, 2])
def test_simulate_reproduces_the_greedy_schedule(strategy: str, operators: int) -> None:
    f = fleet(
        printer("P1", ams=4, loaded=("PLA",)),
        printer("P2", model="A1", ams=4),
        printer("P3", model="A1 Mini"),
        operators=operators,
    )
    jobs = _example_like_jobs()
    greedy = plan(f, jobs, MONDAY_8, strategy=strategy)
    again = simulate(f, jobs, MONDAY_8, sequences_of(greedy), calendar=greedy.calendar)
    assert again.assignments == greedy.assignments
    assert again.strategy == "sequence"


def test_simulate_follows_a_given_order() -> None:
    jobs = [job("A", 600), job("B", 120, copies=3)]
    greedy = plan(fleet(printer()), jobs, MONDAY_8)
    plates = sequences_of(greedy)["P1"]
    reordered = {"P1": [plates[-1], *plates[:-1]]}  # A first, as edd would
    schedule = simulate(fleet(printer()), jobs, MONDAY_8, reordered)
    assert times(schedule.assignments) == [
        ("A", 0, 5, 605),
        ("B #1", 1440, 1445, 1565),
        ("B #2", 1565, 1570, 1690),
        ("B #3", 1690, 1695, 1815),
    ]


def test_simulate_rejects_bad_sequences() -> None:
    f = fleet(printer("A1", model="A1"), printer("P1S"))
    jobs = [job("pla", 60), job("asa", 60, spools=("ASA",))]
    pla, asa = Plate(jobs[0], 1), Plate(jobs[1], 1)
    with pytest.raises(ValueError, match="exactly once"):
        simulate(f, jobs, MONDAY_8, {"A1": [pla]})
    with pytest.raises(ValueError, match="exactly once"):
        simulate(f, jobs, MONDAY_8, {"A1": [pla, pla], "P1S": [asa]})
    with pytest.raises(ValueError, match="unknown printer"):
        simulate(f, jobs, MONDAY_8, {"X": [pla], "P1S": [asa]})
    with pytest.raises(ValueError, match="cannot run on A1"):
        simulate(f, jobs, MONDAY_8, {"A1": [asa], "P1S": [pla]})


def test_dispatcher_copy_is_independent() -> None:
    f = fleet(printer("P1"), printer("P2"))
    jobs = [job("j", 60, copies=2)]
    state = Dispatcher(f, jobs, MONDAY_8, "test")
    branch = state.copy()
    branch.place(0, state.plates[0])
    assert state.schedule.assignments == [] and state.ready == [0.0, 0.0]
    assert state.pool.bookings == [[]]
    assert branch.ready[0] == 65 and branch.slot(1) == 5
