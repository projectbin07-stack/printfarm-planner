from __future__ import annotations

import pytest

from helpers import job, printer
from printfarm_planner.compat import fits_bed, incompatibilities
from printfarm_planner.model import (
    Plate,
    base_material,
    material_allowed,
    material_type,
    normalise_spool,
    split_spools,
    spool_matches,
)


def test_normalise_spool() -> None:
    assert normalise_spool(" pla : Black ") == "PLA:Black"
    assert normalise_spool("petg") == "PETG"
    assert normalise_spool("PLA:") == "PLA"
    assert material_type("PLA:Black") == "PLA"
    with pytest.raises(ValueError):
        normalise_spool(" :red")


def test_plate_label() -> None:
    assert Plate(job("single"), 1).label == "single"
    assert Plate(job("multi", copies=3), 2).label == "multi #2"


def test_compatible_job_has_no_reasons() -> None:
    assert incompatibilities(printer(), job()) == []


def test_material_not_allowed_on_open_frame() -> None:
    reasons = incompatibilities(printer(model="A1"), job(spools=("ASA:grey",)))
    assert reasons == ["material ASA not allowed on this printer"]


def test_custom_material_list_overrides_model() -> None:
    p = printer(model="A1")
    p = type(p)(name="A1", model=p.model, materials=frozenset({"PLA", "ABS"}))
    assert incompatibilities(p, job(spools=("ABS",))) == []
    assert incompatibilities(p, job(spools=("PETG",))) != []


def test_too_many_filaments_for_external_spool() -> None:
    reasons = incompatibilities(printer(ams=0), job(spools=("PLA:white", "PLA:black")))
    assert reasons == ["needs 2 filaments, printer holds 1"]
    assert incompatibilities(printer(ams=4), job(spools=("PLA:white", "PLA:black"))) == []


def test_nozzle_mismatch() -> None:
    reasons = incompatibilities(printer(nozzle=0.6), job(nozzle=0.4))
    assert reasons == ["needs 0.4 mm nozzle, printer has 0.6 mm"]


def test_model_restriction() -> None:
    restricted = job(models=frozenset({"P1S", "A1"}))
    assert incompatibilities(printer(model="A1"), restricted) == []
    reasons = incompatibilities(printer(model="A1 Mini"), restricted)
    assert reasons == ["sliced for A1, P1S, printer is A1 mini"]


def test_bed_fit_with_rotation() -> None:
    mini = printer(model="A1 Mini")  # 180 x 180 x 180
    assert fits_bed((180, 100, 180), mini)
    assert not fits_bed((181, 100, 10), mini)
    assert not fits_bed((100, 100, 181), mini)
    reasons = incompatibilities(mini, job(size=(200, 50, 10)))
    assert reasons == ["200 x 50 x 10 mm does not fit 180 x 180 x 180 mm"]


def test_rotation_matters_on_rectangular_bed() -> None:
    from printfarm_planner.model import Printer, PrinterModel

    model = PrinterModel("Wide", 300, 200, 100, False, frozenset({"PLA"}), frozenset({0.4}))
    p = Printer(name="W", model=model)
    assert fits_bed((150, 280, 50), p)  # only fits rotated
    assert not fits_bed((250, 250, 50), p)


def test_all_reasons_reported_together() -> None:
    reasons = incompatibilities(
        printer(model="A1 Mini", nozzle=0.4),
        job(spools=("ABS", "PLA"), nozzle=0.6, size=(200, 200, 200)),
    )
    assert len(reasons) == 4


@pytest.mark.parametrize("spool", ["PLA-CF", "PLA SILK:gold", "PETG HF:black", "PLA+", "SILK PLA"])
def test_trade_names_run_where_their_base_polymer_is_allowed(spool: str) -> None:
    # Audit #42 repro: PLA-CF, PLA Silk and PETG HF matched no printer ("NO PRINTER").
    assert incompatibilities(printer(model="A1"), job(spools=(spool,))) == []


def test_trade_name_of_a_disallowed_polymer_names_the_polymer() -> None:
    reasons = incompatibilities(printer(model="A1"), job(spools=("ASA AERO",)))
    assert reasons == ["material ASA AERO (ASA) not allowed on this printer"]
    unknown = incompatibilities(printer(model="P1S"), job(spools=("PCTG",)))
    assert unknown == ["material PCTG not allowed on this printer"]


def test_base_material_and_allowed() -> None:
    assert base_material("PLA SILK") == "PLA"
    assert base_material("PETG HF") == "PETG"
    assert base_material("PLA-CF") == "PLA"
    assert base_material("WOOD") is None
    assert material_allowed("PLA-CF", frozenset({"PLA"}))
    assert material_allowed("WOOD", frozenset({"WOOD"}))  # custom names still match exactly
    assert not material_allowed("PETG HF", frozenset({"PLA"}))


def test_trade_name_spool_still_needs_its_own_spool() -> None:
    # Normalising decides where a job may run; a plain PLA spool does not serve PLA-CF.
    assert not spool_matches("PLA:black", "PLA-CF:black")
    assert spool_matches("PLA-CF:black", "PLA CF:black")
    assert spool_matches("PLA SILK:gold", "PLA SILK")


def test_split_spools_escapes() -> None:
    assert split_spools("PLA:white+PETG") == ["PLA:white", "PETG"]
    assert split_spools("PLA+") == ["PLA+"]
    assert split_spools("PLA\\+PETG") == ["PLA+PETG"]
    assert split_spools("PLA++PETG") == ["PLA+", "PETG"]
    assert split_spools("a\\b") == ["a\\b"]  # a backslash not before "+" is kept
