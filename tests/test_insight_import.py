"""gcode-insight -> planner import: colour, sliced-for model, encodings, time zones, conflicts.

The JSON fixtures are gcode-insight 0.2.0 output for its synthetic G-code (tests/fixtures/gcode);
gcode-insight-0.1.0/ holds the older layout for backward compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers import EXAMPLES, FIXTURES, MONDAY_8, fleet, job, printer
from printfarm_planner.cli import main
from printfarm_planner.compat import incompatibilities
from printfarm_planner.fleet import load_fleet
from printfarm_planner.jobs import (
    JobError,
    job_from_insight,
    load_insight_file,
    load_jobs_csv,
    model_from_slicer,
)
from printfarm_planner.model import colour_key, spool_matches
from printfarm_planner.scheduler import plan

V1 = FIXTURES / "gcode-insight-0.1.0"


# ----------------------------------------------------------------- colours and spools


@pytest.mark.parametrize(
    ("label", "key"),
    [("#161616", "black"), ("#000000", "black"), ("#FFFFFF", "white"), ("#ffffffff", "white"),
     ("#FF8000", "orange"), ("#898989", "grey"), ("Gray", "grey"), ("black", "black"),
     ("Galaxy", "galaxy"), ("#12345", "#12345")],
)  # fmt: skip
def test_colour_key(label: str, key: str) -> None:
    assert colour_key(label) == key


@pytest.mark.parametrize(
    ("loaded", "needed", "ok"),
    [("PLA:black", "PLA", True), ("PLA", "PLA:black", True), ("PLA:black", "PLA:#161616", True),
     ("PLA:white", "PLA:#161616", False), ("PETG:black", "PLA:black", False),
     ("PLA:Galaxy", "PLA:galaxy", True), ("PLA:galaxy", "PLA:black", False)],
)  # fmt: skip
def test_spool_matches(loaded: str, needed: str, ok: bool) -> None:
    assert spool_matches(loaded, needed) is ok


def test_job_without_colour_label_uses_a_loaded_spool_of_its_type() -> None:
    # Audit repro: a PLA job on a printer with PLA:black loaded was charged a change.
    p = printer(ams=4, loaded=("PLA:black", "PETG:grey"))
    schedule = plan(fleet(p), [job("bare", 60, spools=("PLA", "PETG"))], MONDAY_8)
    assert [a.material_change for a in schedule.assignments] == [False]
    assert schedule.assignments[0].loaded == ("PLA:black", "PETG:grey")


def test_slicer_hex_colour_matches_named_spool() -> None:
    p = printer(ams=4, loaded=("PLA:black", "PLA:white"))
    jobs = [
        job("white", 60, spools=("PLA:#FFFFFF",), priority=2),
        job("black", 60, spools=("PLA:#161616",), priority=1),
        job("red", 60, spools=("PLA:#E0201C",)),
    ]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    assert [a.material_change for a in schedule.assignments] == [False, False, True]


def test_colour_swaps_between_same_type_are_counted() -> None:
    p = printer(ams=0)
    jobs = [
        job("w", 60, spools=("PLA:#FFFFFF",), priority=2),
        job("b", 60, spools=("PLA:#161616",), priority=1),
    ]
    schedule = plan(fleet(p), jobs, MONDAY_8)
    assert [a.material_change for a in schedule.assignments] == [False, True]


def test_unlabelled_loaded_spool_takes_the_known_colour() -> None:
    p = printer(ams=4, loaded=("PLA",))
    schedule = plan(fleet(p), [job("b", 60, spools=("PLA:black",))], MONDAY_8)
    assert schedule.assignments[0].material_change is False
    assert schedule.assignments[0].loaded == ("PLA:black",)


# ----------------------------------------------------------------- sliced-for model


@pytest.mark.parametrize(
    ("text", "model"),
    [("Bambu Lab P1S", "P1S"), ("Bambu Lab A1 mini", "A1 MINI"), ("MK4S", "MK4S"),
     ("Voron 2.4  350", "VORON 2.4 350")],
)  # fmt: skip
def test_model_from_slicer(text: str, model: str) -> None:
    assert model_from_slicer(text) == model


def test_file_stays_on_the_model_it_was_sliced_for(capsys: pytest.CaptureFixture[str]) -> None:
    # Audit repro: bambu_cube (sliced for P1S) was offered P1S-1, A1-1 and A1-2.
    fleet_path = str(EXAMPLES / "fleet.json")
    bambu, orca = str(FIXTURES / "bambu_cube.json"), str(FIXTURES / "orca_cube.json")
    assert main(["check", "--fleet", fleet_path, bambu, orca]) == 1
    out = capsys.readouterr().out
    assert "bambu_cube  P1S-1\n" in out
    assert "orca_cube   NO PRINTER" in out
    assert "sliced for VORON 2.4 350, printer is P1S" in out


def test_mini_file_matches_the_mini() -> None:
    data = json.loads((FIXTURES / "bambu_cube.json").read_text(encoding="utf-8"))
    data["metadata"]["printer_model"] = "Bambu Lab A1 mini"
    j = job_from_insight(data, "x")
    fl = load_fleet(EXAMPLES / "fleet.json")
    ok = [p.name for p in fl.printers if not incompatibilities(p, j)]
    assert ok == []  # two filaments, and the A1 Mini has only the external spool
    data["tools"] = data["tools"][:1]
    j = job_from_insight(data, "x")
    assert [p.name for p in fl.printers if not incompatibilities(p, j)] == ["A1-Mini"]


def test_csv_models_column_overrides_with_a_warning(tmp_path: Path) -> None:
    (tmp_path / "o.json").write_text(
        (FIXTURES / "orca_cube.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "jobs.csv"
    path.write_text("name,models,gcode_json\no,P1S,o.json\nkeep,,o.json\n", encoding="utf-8")
    over, keep = load_jobs_csv(path, MONDAY_8)
    assert over.models == frozenset({"P1S"})
    assert "warning: CSV models P1S override VORON 2.4 350 that o.json was sliced for" in over.notes
    assert keep.models == frozenset({"VORON 2.4 350"})


def test_newer_schema_is_accepted_with_a_note() -> None:
    data = json.loads((FIXTURES / "prusa_cube.json").read_text(encoding="utf-8"))
    data["schema_version"] = 3
    j = job_from_insight(data, "x")
    assert j.spools == ("PETG:#FF8000",)
    assert any("schema 3 is newer" in n for n in j.notes)


def test_two_colour_job_needs_two_spools_of_the_same_type() -> None:
    data = json.loads((FIXTURES / "bambu_cube.json").read_text(encoding="utf-8"))
    data["metadata"]["filament_type"] = ["PLA", "PLA"]
    for tool in data["tools"]:
        tool["material"] = "PLA"
    j = job_from_insight(data, "x")
    assert j.spools == ("PLA:#FFFFFF", "PLA:#161616")


# ----------------------------------------------------------------- encodings


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")],
)
def test_json_written_by_powershell_is_read(tmp_path: Path, encoding: str, bom: bytes) -> None:
    # Audit repro: `gcode-insight ... --json > x.json` in PowerShell gave "Unexpected UTF-8 BOM".
    text = (FIXTURES / "prusa_cube.json").read_text(encoding="utf-8")
    path = tmp_path / "prusa_cube.json"
    path.write_bytes(bom + text.encode(encoding))
    assert load_insight_file(path).spools == ("PETG:#FF8000",)
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_bytes(b"\xff\xfe" + "name,gcode_json\na,prusa_cube.json\n".encode("utf-16-le"))
    assert load_jobs_csv(csv_path, MONDAY_8)[0].spools == ("PETG:#FF8000",)


def test_fleet_with_bom_is_read(tmp_path: Path) -> None:
    path = tmp_path / "fleet.json"
    path.write_bytes(b"\xef\xbb\xbf" + (EXAMPLES / "fleet.json").read_bytes())
    assert len(load_fleet(path).printers) == 5


def test_undecodable_json_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "x.json"
    path.write_bytes(b"\xff\x00\xfe")
    with pytest.raises(JobError):
        load_insight_file(path)
    assert main(["check", "--fleet", str(EXAMPLES / "fleet.json"), str(path)]) == 2


# ----------------------------------------------------------------- due dates with offsets


def test_due_with_utc_offset_against_naive_start(tmp_path: Path) -> None:
    # Audit repro: a due value with an offset crashed with a TypeError and exit 1.
    path = tmp_path / "jobs.csv"
    path.write_text("name,print_time,material,due\na,60,PLA,2026-11-24T14:00+01:00\n", "utf-8")
    due = load_jobs_csv(path, MONDAY_8)[0].due_min
    local = datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert due == pytest.approx((local - MONDAY_8).total_seconds() / 60)


def test_due_against_aware_start(tmp_path: Path) -> None:
    start = datetime(2026, 11, 23, 8, 0, tzinfo=timezone(timedelta(hours=1)))
    path = tmp_path / "jobs.csv"
    path.write_text(
        "name,print_time,material,due\na,60,PLA,2026-11-23T10:00\nb,60,PLA,2026-11-23T10:00Z\n",
        "utf-8",
    )
    a, b = load_jobs_csv(path, start)
    assert a.due_min == 120  # naive: read in --start's offset
    assert b.due_min == 180  # 10:00 UTC is 11:00 at +01:00


def test_due_with_offset_plans_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text("name,print_time,material,due\na,90,PLA,2026-11-24T14:00+01:00\n", "utf-8")
    code = main(["plan", "--fleet", str(EXAMPLES / "fleet.json"), str(path), "--start",
                 "2026-11-24T08:00"])  # fmt: skip
    assert code == 0
    assert "Production plan" in capsys.readouterr().out


# ----------------------------------------------------------------- CSV vs JSON conflicts


def test_print_forge_style_row_warns_about_stale_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Audit repro: print-forge's rough mass and material silently overrode the JSON.
    (tmp_path / "p.json").write_text(
        (FIXTURES / "prusa_cube.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "jobs.csv"
    path.write_text(
        "name,copies,print_time,material,nozzle_mm,mass_g,models,gcode_json\n"
        "plate-01,1,,PLA:red,0.4,37.7,MK4S,p.json\n",
        encoding="utf-8",
    )
    (j,) = load_jobs_csv(path, MONDAY_8)
    assert j.spools == ("PLA:red",)
    assert "warning: CSV material PLA:red overrides PETG:#FF8000 from p.json" in j.notes
    assert "warning: CSV mass_g 37.7 overrides 0.215 g from p.json" in j.notes
    assert not any("nozzle" in n for n in j.notes)  # same nozzle: no warning


def test_matching_csv_values_raise_no_warning(tmp_path: Path) -> None:
    (tmp_path / "p.json").write_text(
        (FIXTURES / "prusa_cube.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "jobs.csv"
    path.write_text(
        "name,print_time,material,mass_g,gcode_json\na,1,PETG:orange,0.22,p.json\n", "utf-8"
    )
    (j,) = load_jobs_csv(path, MONDAY_8)
    assert not any(n.startswith("warning") for n in j.notes)


def test_warnings_go_to_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "b.json").write_text(
        (FIXTURES / "bambu_cube.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "jobs.csv"
    path.write_text("name,mass_g,gcode_json\na,99,b.json\n", encoding="utf-8")
    assert main(["check", "--fleet", str(EXAMPLES / "fleet.json"), str(path)]) == 0
    err = capsys.readouterr().err
    assert "printfarm-planner: warning: a: CSV mass_g 99 overrides" in err
