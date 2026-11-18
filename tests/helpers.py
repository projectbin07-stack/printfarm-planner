"""Small factories shared by the tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from printfarm_planner.fleet import Fleet, Labour
from printfarm_planner.labour import parse_weekly
from printfarm_planner.model import BUILTIN_MODELS, Job, Printer

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parent.parent / "examples"

#: 2026-11-23 is a Monday.
MONDAY_8 = datetime(2026, 11, 23, 8, 0)
WORKDAY = {"mon-fri": "08:00-18:00"}


def printer(
    name: str = "P1",
    model: str = "P1S",
    *,
    nozzle: float = 0.4,
    ams: int = 0,
    loaded: tuple[str, ...] = (),
    time_factor: float = 1.0,
) -> Printer:
    return Printer(
        name=name,
        model=BUILTIN_MODELS[model.upper()],
        nozzle_mm=nozzle,
        ams_slots=ams,
        loaded=loaded,
        time_factor=time_factor,
    )


def fleet(
    *printers: Printer,
    windows: str | dict[str, str] | None = None,
    operators: int = 1,
    change: float = 5.0,
    swap: float = 10.0,
) -> Fleet:
    return Fleet(
        printers=printers or (printer(),),
        labour=Labour(
            weekly=parse_weekly(windows if windows is not None else WORKDAY),
            operators=operators,
            plate_change_min=change,
            material_change_min=swap,
        ),
    )


def job(
    name: str = "J",
    minutes: float = 60.0,
    *,
    spools: tuple[str, ...] = ("PLA",),
    copies: int = 1,
    due: float | None = None,
    priority: int = 0,
    nozzle: float | None = None,
    size: tuple[float, float, float] | None = None,
    models: frozenset[str] = frozenset(),
    mass: float | None = None,
) -> Job:
    return Job(
        name=name,
        print_time_min=minutes,
        spools=spools,
        copies=copies,
        due_min=due,
        priority=priority,
        nozzle_mm=nozzle,
        size_mm=size,
        models=models,
        mass_g=mass,
    )
