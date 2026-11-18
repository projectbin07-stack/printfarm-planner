from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import replace

from helpers import MONDAY_8, fleet, job, printer
from printfarm_planner.gantt import render_html
from printfarm_planner.metrics import compute_metrics
from printfarm_planner.report import format_duration, format_text, to_csv, to_json_dict
from printfarm_planner.scheduler import Schedule, plan


def overnight() -> Schedule:
    return plan(fleet(printer()), [job("long", 720, copies=2, due=1500, mass=12.5)], MONDAY_8)


def test_format_duration() -> None:
    assert format_duration(0) == "0m"
    assert format_duration(45) == "45m"
    assert format_duration(60) == "1h 00m"
    assert format_duration(1505) == "25h 05m"
    assert format_duration(59.6) == "1h 00m"


def test_text_report() -> None:
    schedule = overnight()
    text = format_text(schedule, compute_metrics(schedule))
    assert "Start Mon 23 Nov 08:00   Finish Tue 24 Nov 20:05   Makespan 36h 05m" in text
    assert "2 plates, 24h 00m printing, fleet utilisation 66.5%" in text  # 1440 / 2165
    assert "11h 55m outside labour windows" in text  # 20:05 -> 08:00
    assert "Filament: 25.0 g" in text
    assert "long #2  Tue 24 Nov 08:05  Tue 24 Nov 20:05  12h 00m  PLA" in text
    assert "LATE 11h 05m" in text  # finish 2165, due 1500


def test_text_report_lists_unscheduled_and_notes() -> None:
    odd = replace(job("abs", 60, spools=("ABS",)), notes=("time: x",))
    schedule = plan(fleet(printer("A1", model="A1")), [odd], MONDAY_8)
    text = format_text(schedule, compute_metrics(schedule))
    assert "Nothing could be scheduled." in text
    assert "A1: material ABS not allowed on this printer" in text
    assert "abs: time: x" in text


def test_csv_export() -> None:
    schedule = overnight()
    rows = list(csv.DictReader(io.StringIO(to_csv(schedule))))
    assert [r["print_start"] for r in rows] == ["2026-11-23T08:05", "2026-11-24T08:05"]
    assert rows[1]["setup_start"] == "2026-11-24T08:00"
    assert rows[0]["print_min"] == "720.0"
    assert rows[0]["material_change"] == "no"
    assert rows[0]["operator"] == "1"


def test_json_export_round_trips() -> None:
    schedule = overnight()
    data = json.loads(json.dumps(to_json_dict(schedule, compute_metrics(schedule))))
    assert data["start"] == "2026-11-23T08:00"
    assert data["strategy"] == "fill"
    assert data["assignments"][1]["print_end_min"] == 2165
    assert data["metrics"]["makespan_min"] == 2165
    assert data["metrics"]["jobs"][0]["late_min"] == 665
    assert data["unscheduled"] == []


def test_html_report_structure() -> None:
    schedule = overnight()
    html = render_html(schedule, compute_metrics(schedule), title="Week <48>")
    assert html.startswith("<!doctype html>")
    assert "<title>Week &lt;48&gt;</title>" in html
    assert html.count("<svg") == 1
    # two print bars coloured as PLA, each with a tooltip
    assert html.count('fill="#3b6fd4"') == 2
    # the overnight wait is drawn once between the two plates
    assert html.count('fill="url(#wait)"') == 1
    assert "waits 11h 55m for an operator" in html
    assert "late by 11h 05m" in html
    # labour windows Mon and Tue are shaded
    assert len(re.findall(r'<rect class="window"', html)) == 2
    assert "@media (prefers-color-scheme: dark)" in html
    assert "<script" not in html


def test_html_escapes_names_and_lists_unscheduled() -> None:
    f = fleet(printer("<P1>"), printer("A1", model="A1"))
    jobs = [job("a&b", 60), job("<abs>", 60, spools=("ABS",), models=frozenset({"A1"}))]
    schedule = plan(f, jobs, MONDAY_8)
    html = render_html(schedule, compute_metrics(schedule))
    assert "&lt;P1&gt;" in html and "<P1>" not in html
    assert "a&amp;b" in html
    assert "<h2>Unscheduled</h2>" in html
    assert "<b>&lt;abs&gt;</b>" in html


def test_html_handles_long_and_material_change_plans() -> None:
    p = replace(printer(loaded=("PETG",)), materials=frozenset({"PLA", "TPU", "HIPS"}))
    jobs = [
        job("x", 3000, copies=4),
        job("tpu", 30, spools=("TPU",)),
        job("odd", 30, spools=("HIPS",)),
    ]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    html = render_html(schedule, compute_metrics(schedule))
    assert "incl. material change" in html
    assert "#6b6b6b" in html  # HIPS has no palette colour
    assert "Sat 28 Nov" in html
