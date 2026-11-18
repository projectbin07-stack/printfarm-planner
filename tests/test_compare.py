"""compare command, objective output in plan, and the comparison report."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from helpers import EXAMPLES, MONDAY_8, fleet, job, printer, random_instance
from printfarm_planner.cli import main
from printfarm_planner.compare import compare, compare_json, format_compare, render_compare_html
from printfarm_planner.gantt import render_html
from printfarm_planner.metrics import compute_metrics
from printfarm_planner.objective import Weights, evaluate
from printfarm_planner.report import format_text, to_json_dict
from printfarm_planner.scheduler import plan
from printfarm_planner.search import SearchConfig

FLEET = str(EXAMPLES / "fleet.json")
JOBS = str(EXAMPLES / "jobs.csv")
START = "2026-11-23T08:00"
FAST = ["--iterations", "60"]


def test_compare_runs_every_method_on_a_small_instance() -> None:
    f, jobs = random_instance(4, 6)
    runs = compare(f, jobs, MONDAY_8, config=SearchConfig(iterations=100))
    assert [r.method for r in runs] == ["fill", "edd", "hill", "anneal", "exact"]
    totals = {r.method: r.objective.total for r in runs if r.objective is not None}
    assert totals["exact"] <= min(totals.values()) + 1e-9
    assert totals["hill"] <= min(totals["fill"], totals["edd"]) + 1e-9
    assert runs[-1].note.startswith("optimal in the sequence model")
    assert runs[2].note.startswith("from ")

    text = format_compare(runs, Weights())
    assert text.startswith("Planner comparison (6 plates)")
    assert "objective = 1 x tardiness + 1 x makespan + 0.2 x wait off" in text
    exact_line = next(line for line in text.splitlines() if line.lstrip().startswith("exact"))
    assert " 0.0% " in exact_line

    data = compare_json(runs, Weights(), MONDAY_8)
    assert [m["method"] for m in data["methods"]] == ["fill", "edd", "hill", "anneal", "exact"]
    assert data["methods"][-1]["gap"] == 0
    assert data["start"] == "2026-11-23T08:00"

    html = render_compare_html(runs, Weights(), "Cmp <test>")
    assert "<title>Cmp &lt;test&gt;</title>" in html
    assert "Best schedule: " in html and "<svg" in html
    assert "<script" not in html


def test_compare_skips_exact_above_the_limit() -> None:
    f, jobs = random_instance(4, 6)
    runs = compare(f, jobs, MONDAY_8, config=SearchConfig(iterations=10), exact_limit=5)
    exact = runs[-1]
    assert exact.objective is None and exact.note.startswith("skipped: 6 plates")
    assert "skipped: 6 plates is above" in format_compare(runs, Weights())
    assert compare_json(runs, Weights(), MONDAY_8)["methods"][-1]["objective"] is None


def test_compare_reports_an_unfinished_exact_search() -> None:
    f, jobs = random_instance(4, 7)
    runs = compare(f, jobs, MONDAY_8, config=SearchConfig(iterations=5), node_limit=2)
    assert "node limit 2 reached" in runs[-1].note


def test_zero_objective_has_zero_gap() -> None:
    f = fleet(printer())
    weights = Weights(tardiness=1, makespan=0, wait_off=0, wait_in=0, material_changes=0)
    runs = compare(f, [job("a", 60)], MONDAY_8, weights, SearchConfig(iterations=5))
    assert all(r.objective is not None and r.objective.total == 0 for r in runs)
    assert "0.0%" in format_compare(runs, weights)
    assert all(m["gap"] is None for m in compare_json(runs, weights, MONDAY_8)["methods"])


def test_plan_reports_objective_in_text_json_and_html() -> None:
    schedule = plan(fleet(printer()), [job("long", 720, copies=2, due=2000)], MONDAY_8)
    metrics = compute_metrics(schedule)
    objective = evaluate(schedule, Weights(), metrics)
    text = format_text(schedule, metrics, objective)
    assert "Objective (lower is better; minutes, weighted)" in text
    assert re.search(r"weighted tardiness\s+165 min\s+1\s+165\.0", text)
    assert re.search(r"wait outside labour hours\s+715 min\s+0\.2\s+143\.0", text)
    assert re.search(r"total\s+2473\.0", text)  # 165 + 2165 + 143
    data = to_json_dict(schedule, metrics, objective)
    assert data["objective"]["total"] == 2473
    assert data["objective"]["terms"]["makespan"] == 2165
    html = render_html(schedule, metrics, "t", objective)
    assert "<h2>Objective</h2>" in html and "2473.0" in html
    assert "Objective" not in format_text(schedule, metrics)


def test_cli_compare_text_json_and_html(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html = tmp_path / "compare.html"
    args = ["compare", "--fleet", FLEET, JOBS, "--start", START, *FAST]
    assert main([*args, "--html", str(html)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Planner comparison (26 plates)")
    assert "skipped: 26 plates is above the exact-solver limit of 8" in out
    assert "<svg" in html.read_text(encoding="utf-8")

    assert main([*args, "--json", "--weight", "makespan=2"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weights"]["makespan"] == 2
    fill, edd, hill = (data["methods"][i]["objective"]["total"] for i in range(3))
    assert hill <= min(fill, edd)


def test_cli_compare_unschedulable_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("name,print_time,material\nok,60,PLA\nbig,60,PC+PA+ABS+ASA+PLA\n", "utf-8")
    assert main(["compare", "--fleet", FLEET, str(jobs), "--start", START, *FAST]) == 1
    captured = capsys.readouterr()
    assert "Unscheduled (no printer can run these): big" in captured.err
    assert "optimal in the sequence model" in captured.out


def test_cli_plan_improve(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["plan", "--fleet", FLEET, JOBS, "--start", START, "--json", *FAST]
    assert main(args) == 0
    greedy = json.loads(capsys.readouterr().out)
    assert main([*args, "--improve", "hill", "--seed", "3"]) == 0
    improved = json.loads(capsys.readouterr().out)
    assert improved["strategy"] == "fill+hill"
    assert len(improved["assignments"]) == 26
    assert improved["objective"]["total"] <= greedy["objective"]["total"]
    assert main([*args, "--improve", "anneal", "--time-limit", "0.2"]) == 0
    assert json.loads(capsys.readouterr().out)["strategy"] == "fill+anneal"


def test_cli_plan_html_has_objective(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html = tmp_path / "plan.html"
    assert main(["plan", "--fleet", FLEET, JOBS, "--start", START, "--html", str(html)]) == 0
    assert "Objective (lower is better" in capsys.readouterr().out
    assert "<h2>Objective</h2>" in html.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "extra",
    [["--weight", "speed=1"], ["--weight", "makespan=-2"]],
)
def test_cli_bad_weight_exits_2(extra: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", "--fleet", FLEET, JOBS, "--start", START, *extra]) == 2
    assert "weight" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [["--iterations", "-1"], ["--time-limit", "0"], ["--exact-limit", "-3"]],
)
def test_cli_bad_search_options_are_usage_errors(extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["compare", "--fleet", FLEET, JOBS, *extra])
    assert exc.value.code == 2
