"""Objective terms checked against hand-derived values."""

from __future__ import annotations

import pytest

from helpers import MONDAY_8, fleet, job, printer
from printfarm_planner.metrics import compute_metrics
from printfarm_planner.objective import TERMS, Objective, Weights, evaluate, job_weight
from printfarm_planner.scheduler import plan


def test_terms_of_an_overnight_plan() -> None:
    # Hand timeline (Mon-Fri 08:00-18:00): long #1 prints 5-725, waits until Tue 08:00
    # (minute 1440), long #2 prints 1445-2165 and waits until Wed 08:00 (2880) to be
    # cleared, which ends the plan at 2885. Due at 2000 with priority 2, so w = 3;
    # tardiness uses the print finish (2165).
    jobs = [job("long", 720, copies=2, due=2000, priority=2)]
    objective = evaluate(plan(fleet(printer()), jobs, MONDAY_8))
    assert objective.makespan == 2885
    assert objective.wait_off == pytest.approx((1440 - 725) + (2880 - 2165))
    assert objective.wait_in == 0
    assert objective.material_changes == 0
    assert objective.tardiness == pytest.approx(3 * (2165 - 2000))
    # defaults: 1 x tardiness + 1 x makespan + 0.2 x wait off + 0.5 x wait in + 10 x changes
    assert objective.total == pytest.approx(495 + 2885 + 0.2 * 1430)


def test_material_changes_and_wait_in_are_weighted() -> None:
    p = printer(loaded=("PLA",))
    jobs = [job("petg", 60, spools=("PETG",), priority=1), job("pla", 60, spools=("PLA",))]
    weights = Weights(tardiness=0, makespan=0, wait_off=0, wait_in=0, material_changes=7)
    objective = evaluate(plan(fleet(p), jobs, MONDAY_8), weights)
    assert objective.material_changes == 2
    assert objective.contributions()["material_changes"] == 14
    assert objective.total == 14

    # 593 min prints end 17:58 on Monday and Tuesday: 2 minutes of in-hours waiting
    # before each night (the second before its clearing on Wednesday)
    sched = plan(fleet(printer()), [job("j", 593, copies=2)], MONDAY_8)
    only_in = evaluate(sched, Weights(tardiness=0, makespan=0, wait_off=0, wait_in=1))
    assert only_in.wait_in == pytest.approx(4)
    assert only_in.total == pytest.approx(4)


def test_job_weight_from_priority() -> None:
    assert job_weight(job(priority=0)) == 1
    assert job_weight(job(priority=-3)) == 1
    assert job_weight(job(priority=4)) == 5


def test_evaluate_reuses_given_metrics() -> None:
    schedule = plan(fleet(printer()), [job("a", 60, due=30)], MONDAY_8)
    metrics = compute_metrics(schedule)
    assert evaluate(schedule, metrics=metrics) == evaluate(schedule)
    assert evaluate(schedule).tardiness == 65 - 30


def test_weights_parse_and_validate() -> None:
    weights = Weights.parse(["makespan=0.5", "material-changes=0", " wait_off = 2"])
    assert weights == Weights(makespan=0.5, material_changes=0, wait_off=2)
    assert Weights.parse([]) == Weights()
    with pytest.raises(ValueError, match="NAME=VALUE"):
        Weights.parse(["speed=1"])
    with pytest.raises(ValueError, match="NAME=VALUE"):
        Weights.parse(["makespan"])
    with pytest.raises(ValueError, match="not a number"):
        Weights.parse(["makespan=fast"])
    with pytest.raises(ValueError, match=">= 0"):
        Weights(makespan=-1)
    with pytest.raises(ValueError, match=">= 0"):
        Weights(wait_in=float("nan"))


def test_to_dict_and_comparison() -> None:
    w = Weights()
    a = Objective(w, tardiness=10, makespan=100, wait_off=50, wait_in=4, material_changes=1)
    b = Objective(w, tardiness=0, makespan=100, wait_off=50, wait_in=4, material_changes=1)
    data = a.to_dict()
    assert list(data["terms"]) == list(TERMS)
    assert data["total"] == pytest.approx(10 + 100 + 10 + 2 + 10)
    assert data["contributions"]["wait_off"] == 10
    assert data["weights"]["material_changes"] == 10
    assert b.better_than(a) and not a.better_than(b) and not a.better_than(a)
