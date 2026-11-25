"""The vendored registry loads, and the built-in models are exactly its printers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from printfarm_planner.jobs import load_jobs_csv
from printfarm_planner.model import BUILTIN_MODELS
from printfarm_planner.registry import load_registry


def test_builtin_models_come_from_the_registry() -> None:
    reg = load_registry()
    assert [m.registry_key for m in BUILTIN_MODELS.values()] == [p.key for p in reg.printers]
    for spec in reg.printers:
        model = BUILTIN_MODELS[spec.model.upper()]
        assert (model.bed_x_mm, model.bed_y_mm, model.bed_z_mm) == spec.build_volume_mm
        assert model.enclosed is spec.enclosed
        assert model.nozzles_mm == frozenset(spec.nozzle_options_mm)
        assert model.max_spools == spec.ams_max_slots
        assert spec.sources


def test_csv_models_column_accepts_registry_aliases(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("name,print_time,material,models\nA,60,PLA,X1C+a1-mini\n", encoding="utf-8")
    (job,) = load_jobs_csv(jobs, datetime(2026, 11, 23, 8, 0))
    assert job.models == frozenset({"X1 CARBON", "A1 MINI"})
