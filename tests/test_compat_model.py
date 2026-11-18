from __future__ import annotations

import pytest

from helpers import job, printer
from printfarm_planner.compat import fits_bed, incompatibilities
from printfarm_planner.model import Plate, material_type, normalise_spool


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
    assert reasons == ["sliced for A1, P1S, printer is A1 Mini"]


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
