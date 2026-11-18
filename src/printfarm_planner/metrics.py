"""Utilisation and lateness figures derived from a schedule."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

from .scheduler import Schedule

__all__ = ["JobResult", "Metrics", "PrinterStats", "compute_metrics"]


@dataclass(frozen=True, slots=True)
class PrinterStats:
    name: str
    model: str
    plates: int
    print_min: float
    setup_min: float
    #: Finished plate sitting on the bed, split by whether a labour window was open
    #: (in-hours waits come from operator contention or a window closing soon).
    wait_off_hours_min: float
    wait_in_hours_min: float
    material_changes: int
    utilisation: float  # print time / makespan
    finish_min: float


@dataclass(frozen=True, slots=True)
class JobResult:
    name: str
    copies: int
    first_start_min: float
    finish_min: float
    due_min: float | None
    late_min: float  # 0 when on time or no due date
    printers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Metrics:
    makespan_min: float
    plates: int
    print_min: float
    fleet_utilisation: float
    operator_min: float
    operator_tasks: int
    material_changes: int
    wait_off_hours_min: float
    wait_in_hours_min: float
    mass_g: float | None
    late_jobs: int
    unscheduled_jobs: int
    printers: tuple[PrinterStats, ...]
    jobs: tuple[JobResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _overlap(calendar_windows: list[tuple[float, float]]) -> float:
    return sum(hi - lo for lo, hi in calendar_windows)


def compute_metrics(schedule: Schedule) -> Metrics:
    cal = schedule.calendar
    makespan = max((a.end for a in schedule.assignments), default=0.0)
    printer_stats: list[PrinterStats] = []
    total_off = total_busy = 0.0
    for printer in schedule.printers:
        rows = schedule.for_printer(printer.name)
        off = busy = 0.0
        for previous, current in pairwise(rows):
            gap_start, gap_end = previous.end, current.setup_start
            if gap_end > gap_start:
                inside = _overlap(list(cal.windows_between(gap_start, gap_end)))
                busy += inside
                off += (gap_end - gap_start) - inside
        print_min = sum(a.print_min for a in rows)
        printer_stats.append(
            PrinterStats(
                name=printer.name,
                model=printer.model.name,
                plates=len(rows),
                print_min=print_min,
                setup_min=sum(a.setup_min for a in rows),
                wait_off_hours_min=off,
                wait_in_hours_min=busy,
                material_changes=sum(a.material_change for a in rows),
                utilisation=print_min / makespan if makespan > 0 else 0.0,
                finish_min=max((a.end for a in rows), default=0.0),
            )
        )
        total_off += off
        total_busy += busy

    job_results: list[JobResult] = []
    for job in schedule.jobs:
        rows = [a for a in schedule.assignments if a.plate.job.name == job.name]
        if not rows:
            continue
        finish = max(a.end for a in rows)
        late = max(0.0, finish - job.due_min) if job.due_min is not None else 0.0
        job_results.append(
            JobResult(
                name=job.name,
                copies=job.copies,
                first_start_min=min(a.start for a in rows),
                finish_min=finish,
                due_min=job.due_min,
                late_min=late,
                printers=tuple(sorted({a.printer for a in rows})),
            )
        )

    total_print = sum(s.print_min for s in printer_stats)
    masses = [a.plate.job.mass_g for a in schedule.assignments]
    known = [m for m in masses if m is not None]
    capacity = makespan * len(schedule.printers)
    return Metrics(
        makespan_min=makespan,
        plates=len(schedule.assignments),
        print_min=total_print,
        fleet_utilisation=total_print / capacity if capacity > 0 else 0.0,
        operator_min=sum(a.setup_min for a in schedule.assignments),
        operator_tasks=len(schedule.assignments),
        material_changes=sum(a.material_change for a in schedule.assignments),
        wait_off_hours_min=total_off,
        wait_in_hours_min=total_busy,
        mass_g=sum(known) if known and len(known) == len(masses) else None,
        late_jobs=sum(1 for j in job_results if j.late_min > 0),
        unscheduled_jobs=len(schedule.unscheduled),
        printers=tuple(printer_stats),
        jobs=tuple(job_results),
    )
