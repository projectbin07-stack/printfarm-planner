from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from helpers import EXAMPLES
from printfarm_planner.fleet import FleetError, load_fleet, parse_fleet


def minimal(**printer: Any) -> dict[str, Any]:
    return {"printers": [{"name": "P", "model": "P1S", **printer}]}


def test_example_fleet_loads() -> None:
    fleet = load_fleet(EXAMPLES / "fleet.json")
    assert [p.name for p in fleet.printers] == ["P1S-1", "P1S-2", "A1-1", "A1-2", "A1-Mini"]
    assert fleet.printers[0].loaded[0] == "PETG:black"
    assert fleet.printers[4].model.name == "A1 Mini"
    assert fleet.printers[4].time_factor == 1.1
    assert fleet.labour.weekly[5] == [(540, 780)]
    assert fleet.labour.plate_change_min == 5
    assert fleet.labour.material_change_min == 10


def test_defaults() -> None:
    fleet = parse_fleet(minimal())
    p = fleet.printers[0]
    assert (p.nozzle_mm, p.ams_slots, p.spool_capacity, p.time_factor) == (0.4, 0, 1, 1.0)
    assert p.allowed_materials == p.model.materials
    assert fleet.labour.weekly[0] == [(480, 1080)]
    assert fleet.labour.weekly[6] == []
    assert fleet.labour.operators == 1


def test_model_lookup_is_case_insensitive_and_materials_override() -> None:
    fleet = parse_fleet(minimal(model="a1 mini", materials=["pla", "petg"]))
    assert fleet.printers[0].model.bed_x_mm == 180
    assert fleet.printers[0].allowed_materials == frozenset({"PLA", "PETG"})


def test_custom_model() -> None:
    data = {
        "models": {
            "Big": {"bed_mm": [350, 350, 400], "enclosed": True, "materials": ["PLA", "ABS"]}
        },
        "printers": [{"name": "B", "model": "big", "nozzle_mm": 0.6}],
    }
    p = parse_fleet(data).printers[0]
    assert (p.model.name, p.model.bed_z_mm, p.model.enclosed) == ("Big", 400, True)
    assert p.model.materials == frozenset({"PLA", "ABS"})


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "JSON object"),
        ({}, "printers"),
        ({"printers": []}, "printers"),
        ({"printers": ["P1S"]}, "object"),
        ({"printers": [{"model": "P1S"}]}, "without a name"),
        (minimal(model="X1C"), "unknown model"),
        (minimal(colour="red"), "unknown key"),
        (minimal(nozzle_mm=0.5), "0.5"),
        (minimal(nozzle_mm="0.4"), "number"),
        (minimal(ams_slots=-1), "ams_slots"),
        (minimal(ams_slots=True), "ams_slots"),
        (minimal(count=0), "count"),
        (minimal(time_factor=0), "time_factor"),
        (minimal(materials="PLA"), "list of strings"),
        (minimal(materials=[1]), "list of strings"),
        (minimal(loaded=["PLA", "PETG"]), "only 1 fit"),
        (minimal(loaded=[":x"]), "empty material"),
        ({**minimal(), "labour": []}, "labour must be"),
        ({**minimal(), "labour": {"shifts": 1}}, "unknown key"),
        ({**minimal(), "labour": {"windows": "weekdays"}}, "labour windows"),
        ({**minimal(), "labour": {"operators": 0}}, "operators"),
        ({**minimal(), "labour": {"plate_change_min": -1}}, "plate_change_min"),
        ({"models": [], "printers": []}, "models must be"),
        ({"models": {"M": []}, **minimal()}, "definition"),
        ({"models": {"M": {"bed_mm": [1, 2]}}, **minimal()}, "bed_mm"),
        ({"models": {"M": {"bed_mm": [1, 2, 3], "materials": ["PLA"], "nozzles_mm": 4}}}, "list"),
    ],
)
def test_invalid(data: Any, message: str) -> None:
    with pytest.raises(FleetError, match=message):
        parse_fleet(data)


def test_count_expands_and_duplicates_rejected() -> None:
    data = {"printers": [{"name": "A", "model": "A1", "count": 2}, {"name": "A-2", "model": "A1"}]}
    with pytest.raises(FleetError, match="duplicate printer name"):
        parse_fleet(data)


def test_load_errors_name_the_file(tmp_path: Path) -> None:
    bad = tmp_path / "fleet.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(FleetError, match=r"fleet\.json"):
        load_fleet(bad)
    bad.write_text(json.dumps({"printers": []}), encoding="utf-8")
    with pytest.raises(FleetError, match=r"fleet\.json.*printers"):
        load_fleet(bad)
    with pytest.raises(FleetError):
        load_fleet(tmp_path / "missing.json")
