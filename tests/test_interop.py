"""Live interop with gcode-insight: skipped unless gcode-insight is installed.

CI installs the pinned release (see the `interop` job in .github/workflows/ci.yml) and runs this
file, so a change to gcode-insight's JSON that breaks the import is caught here rather than by
the committed fixtures, which only show what an older release wrote.
"""

from __future__ import annotations

import json

import pytest

from helpers import FIXTURES
from printfarm_planner.jobs import job_from_insight

gi = pytest.importorskip("gcode_insight")

GCODE = FIXTURES / "gcode"


def test_installed_version_writes_schema_4_or_later() -> None:
    major, minor = (int(p) for p in gi.__version__.split(".")[:2])
    assert (major, minor) >= (0, 4)
    assert gi.SCHEMA_VERSION >= 4


@pytest.mark.parametrize(
    "name", ["bambu_cube", "prusa_cube", "orca_cube", "marlin_generic", "cura_griffin"]
)
def test_live_output_imports_like_the_committed_fixture(name: str) -> None:
    live = gi.analyze_file(GCODE / f"{name}.gcode").to_dict()
    # Round-trip through JSON text, as the CLI pipeline does.
    live = json.loads(json.dumps(live))
    committed = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    for key in ("schema_version", "source", "tools", "bounding_box", "mass_g"):
        assert live[key] == committed[key], key
    for key in (
        "filament_type",
        "filament_colour",
        "printer_model",
        "estimated_time_s",
        "estimated_time_source",
    ):
        assert live["metadata"][key] == committed["metadata"][key], key
    default = "PLA" if name in ("marlin_generic", "cura_griffin") else None
    assert job_from_insight(live, name, default_material=default) == job_from_insight(
        committed, name, default_material=default
    )


def test_live_bambu_job_carries_colour_and_model() -> None:
    data = gi.analyze_file(GCODE / "bambu_cube.gcode").to_dict()
    job = job_from_insight(data, "bambu_cube")
    assert job.spools == ("PLA:#FFFFFF", "PETG:#161616")
    assert job.models == frozenset({"P1S"})


def test_live_cura_print_time_becomes_the_job_time() -> None:
    data = gi.analyze_file(GCODE / "cura_griffin.gcode").to_dict()
    job = job_from_insight(data, "cura_griffin", default_material="PLA")
    assert job.print_time_min == pytest.approx(95 / 60)
    assert "time: slicer total estimate from ;PRINT.TIME" in job.notes
