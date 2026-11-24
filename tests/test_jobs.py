"""Job loading. The JSON fixtures are real `gcode-insight analyze --json` output (0.2.0, and 0.1.0
in tests/fixtures/gcode-insight-0.1.0) for gcode-insight's synthetic G-code fixtures (see
tests/fixtures/README.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import EXAMPLES, FIXTURES, MONDAY_8
from printfarm_planner.jobs import (
    JobError,
    job_from_insight,
    load_insight_file,
    load_jobs_csv,
    parse_duration_min,
)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("90", 90), ("12.5", 12.5), ("1:30", 90), ("1h30m", 90), ("2h", 120), ("45m", 45),
     ("1.5h", 90), ("1h 5min", 65), (" 0:07 ", 7)],
)  # fmt: skip
def test_parse_duration(text: str, minutes: float) -> None:
    assert parse_duration_min(text) == minutes


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "0:00", "1x", "h"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(JobError):
        parse_duration_min(text)


def test_bambu_analysis() -> None:
    job = load_insight_file(FIXTURES / "bambu_cube.json", copies=3)
    assert job.name == "bambu_cube"
    # metadata.estimated_time_s = 393 s -> 6.55 min
    assert job.print_time_min == pytest.approx(393 / 60)
    # filament_colour = #FFFFFF;#161616, printer_model = Bambu Lab P1S
    assert job.spools == ("PLA:#FFFFFF", "PETG:#161616")
    assert job.models == frozenset({"P1S"})
    assert job.copies == 3
    assert job.nozzle_mm == 0.4
    # bounding box X 18..137.79, Y 1..137.79, max Z 1.0
    assert job.size_mm == (119.79, 136.79, 1.0)
    assert job.mass_g == pytest.approx(0.2184345)
    assert job.notes == ("time: slicer total estimate", "sliced for Bambu Lab P1S")


def test_gcode_insight_0_1_0_output_still_loads() -> None:
    job = load_insight_file(FIXTURES / "gcode-insight-0.1.0" / "bambu_cube.json")
    # 0.1.0 had no colour and no printer model: bare types, any compatible printer
    assert job.spools == ("PLA", "PETG")
    assert job.models == frozenset()
    assert job.print_time_min == pytest.approx(393 / 60)


def test_unknown_material_needs_a_default() -> None:
    with pytest.raises(JobError, match="material of T0 is unknown"):
        load_insight_file(FIXTURES / "marlin_generic.json")
    job = load_insight_file(FIXTURES / "marlin_generic.json", default_material="PETG", prep_min=4)
    assert job.spools == ("PETG",)
    # no slicer estimate: model 33.9535 s + 4 min preparation
    assert job.print_time_min == pytest.approx(33.953547994367696 / 60 + 4)
    assert job.nozzle_mm is None
    assert "T0 material assumed PETG" in job.notes


def test_material_falls_back_to_metadata_filament_type() -> None:
    data = json.loads((FIXTURES / "prusa_cube.json").read_text(encoding="utf-8"))
    data["tools"][0]["material"] = None
    assert job_from_insight(data, "x").spools == ("PETG:#FF8000",)


def test_not_an_analysis() -> None:
    with pytest.raises(JobError, match="not a gcode-insight analysis"):
        job_from_insight({"a": 1}, "x")


def test_no_time_or_no_extrusion() -> None:
    data = json.loads((FIXTURES / "prusa_cube.json").read_text(encoding="utf-8"))
    data["metadata"]["estimated_time_s"] = None
    data["estimated_time_s"] = 0
    with pytest.raises(JobError, match="time estimate"):
        job_from_insight(data, "x")
    data["estimated_time_s"] = 60
    data["tools"] = [{"tool": 0, "filament_mm": 0}]
    with pytest.raises(JobError, match="no extrusion"):
        job_from_insight(data, "x")


def test_bad_json_files(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text("[1]", encoding="utf-8")
    with pytest.raises(JobError, match="JSON object"):
        load_insight_file(bad)
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(JobError):
        load_insight_file(bad)


def test_example_csv() -> None:
    jobs = load_jobs_csv(EXAMPLES / "jobs.csv", MONDAY_8)
    by_name = {j.name: j for j in jobs}
    assert len(jobs) == 7
    lid = by_name["enclosure-lid"]
    assert (lid.copies, lid.print_time_min, lid.spools) == (2, 570, ("PETG:black",))
    assert lid.due_min == 48 * 60
    assert lid.models == frozenset({"P1S", "A1"})
    assert lid.size_mm == (220, 160, 30)
    assert lid.priority == 1
    badge = by_name["logo-badge"]
    assert badge.spools == ("PLA:white", "PLA:orange")
    assert badge.print_time_min == 145
    cube = by_name["calibration-cube"]
    # time, spools, printer model and nozzle all from the JSON (393 s slicer total)
    assert cube.print_time_min == pytest.approx(393 / 60)
    assert cube.spools == ("PLA:#FFFFFF", "PETG:#161616")
    assert cube.models == frozenset({"P1S"})
    assert cube.nozzle_mm == 0.4
    assert cube.source == "../tests/fixtures/bambu_cube.json"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "jobs.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_iso_due_and_blank_rows(tmp_path: Path) -> None:
    path = write(tmp_path, "name,print_time,material,due\n\na,60,pla,2026-11-24T08:00\n,,,\n")
    jobs = load_jobs_csv(path, MONDAY_8)
    assert len(jobs) == 1
    assert jobs[0].due_min == 1440
    assert jobs[0].spools == ("PLA",)


def test_csv_json_overrides(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(
        (FIXTURES / "bambu_cube.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = write(
        tmp_path,
        "name,print_time,nozzle_mm,size_mm,mass_g,gcode_json\na,2h,0.6,10x20x30,55,a.json\n",
    )
    job = load_jobs_csv(path, MONDAY_8)[0]
    assert job.print_time_min == 120
    assert job.spools == ("PLA:#FFFFFF", "PETG:#161616")
    assert (job.nozzle_mm, job.size_mm, job.mass_g) == (0.6, (10, 20, 30), 55)
    # The CSV wins, but each value far from the analysis is reported.
    assert job.notes == (
        "warning: CSV print_time 120 min overrides 7 min from a.json",
        "warning: CSV mass_g 55 overrides 0.218 g from a.json",
        "warning: CSV nozzle_mm 0.6 overrides 0.4 from a.json",
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("print_time\n60\n", "missing required column 'name'"),
        ("name,colour\na,red\n", "unknown column"),
        ("name,print_time,material\n,60,PLA\n", "name is empty"),
        ("name,material\na,PLA\n", "print_time is empty"),
        ("name,print_time\na,60\n", "material is empty"),
        ("name,print_time,material\na,60,PLA+\n", "empty material"),
        ("name,print_time,material,copies\na,60,PLA,0\n", "copies must be at least 1"),
        ("name,print_time,material,copies\na,60,PLA,two\n", "whole number"),
        ("name,print_time,material,priority\na,60,PLA,high\n", "whole number"),
        ("name,print_time,material,size_mm\na,60,PLA,10x20\n", "bad size"),
        ("name,print_time,material,size_mm\na,60,PLA,10xAx2\n", "bad size"),
        ("name,print_time,material,size_mm\na,60,PLA,0x2x2\n", "positive"),
        ("name,print_time,material,mass_g\na,60,PLA,heavy\n", "not a number"),
        ("name,print_time,material,nozzle_mm\na,60,PLA,-1\n", "positive"),
        ("name,print_time,material,due\na,60,PLA,tomorrow\n", "bad due"),
        ("name,print_time,material\na,60,PLA\na,30,PLA\n", "duplicate job name"),
        ("name,gcode_json\na,missing.json\n", "gcode_json missing.json"),
    ],
)
def test_csv_errors(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(JobError, match=message):
        load_jobs_csv(write(tmp_path, text), MONDAY_8)


def test_csv_error_names_the_row(tmp_path: Path) -> None:
    path = write(tmp_path, "name,print_time,material\nok,60,PLA\nbad,xx,PLA\n")
    with pytest.raises(JobError, match=r"jobs.csv row 3"):
        load_jobs_csv(path, MONDAY_8)


def test_csv_json_must_be_object(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    with pytest.raises(JobError, match="JSON object"):
        load_jobs_csv(write(tmp_path, "name,gcode_json\na,a.json\n"), MONDAY_8)


def test_missing_csv(tmp_path: Path) -> None:
    with pytest.raises(JobError):
        load_jobs_csv(tmp_path / "nope.csv", MONDAY_8)
