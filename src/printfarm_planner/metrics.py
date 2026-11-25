"""Utilisation and lateness figures derived from a schedule."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

from .scheduler import Schedule

__all__ = ["JobResult", "Metrics", "PrinterStats", "compute_metrics"]


@dataclass(frozen=True, slots=True)
class PrinterStats:
    """Figures for one printer in a schedule (minutes, utilisation as a fraction)."""

    name: str
    model: str
    plates: int
    print_min: float
    setup_min: float
    #: Finished plate sitting on the bed, split by whether a labour window was open
    #: (in-hours waits come from operator contention or a window closing soon).
    #: Includes the final plate's wait until it is cleared.
    wait_off_hours_min: float
    wait_in_hours_min: float
    material_changes: int
    utilisation: float  # print time / makespan
    finish_min: float  # last plate finished printing
    #: of the waiting above, the final plate's wait until it is cleared
    final_wait_min: float
    #: final plate cleared (end of the clearing task); equals finish_min if none
    cleared_min: float


@dataclass(frozen=True, slots=True)
class JobResult:
    """When a scheduled job starts and finishes, and how late it is."""

    name: str
    copies: int
    first_start_min: float
    finish_min: float
    due_min: float | None
    late_min: float  # 0 when on time or no due date
    printers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Metrics:
    """Fleet-wide figures for a schedule, plus per-printer and per-job rows."""

    makespan_min: float
    plates: int
    print_min: float
    fleet_utilisation: float
    operator_min: float
    operator_tasks: int
    material_changes: int
    wait_off_hours_min: float
    wait_in_hours_min: float
    #: of the waiting above, final plates waiting to be cleared
    final_wait_min: float
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
    """Figures for ``schedule``. The makespan ends with the last clearing task."""
    cal = schedule.calendar
    makespan = max(
        [a.end for a in schedule.assignments] + [c.end for c in schedule.clearings], default=0.0
    )
    printer_stats: list[PrinterStats] = []
    total_off = total_busy = total_final = 0.0
    for printer in schedule.printers:
        rows = schedule.for_printer(printer.name)
        clearing = schedule.clearing_for(printer.name)
        # each finished plate waits until the operator task after it starts: the
        # next plate's setup or, for the final plate, its clearing
        gaps = [(previous.end, current.setup_start) for previous, current in pairwise(rows)]
        if rows and schedule.state_of(printer.name).plate_on_bed:
            # a plate already on the bed waits for the first plate change as well
            gaps.insert(0, (rows[0].ready, rows[0].setup_start))
        final = 0.0
        if clearing is not None:
            gaps.append((clearing.ready, clearing.start))
            final = max(0.0, clearing.start - clearing.ready)
        off = busy = 0.0
        for gap_start, gap_end in gaps:
            if gap_end > gap_start:
                inside = _overlap(list(cal.windows_between(gap_start, gap_end)))
                busy += inside
                off += (gap_end - gap_start) - inside
        print_min = sum(a.print_min for a in rows)
        finish = max((a.end for a in rows), default=0.0)
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
                finish_min=finish,
                final_wait_min=final,
                cleared_min=clearing.end if clearing is not None else finish,
            )
        )
        total_off += off
        total_busy += busy
        total_final += final

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
        operator_min=sum(a.setup_min for a in schedule.assignments)
        + sum(c.end - c.start for c in schedule.clearings),
        operator_tasks=len(schedule.assignments) + len(schedule.clearings),
        material_changes=sum(a.material_change for a in schedule.assignments),
        wait_off_hours_min=total_off,
        wait_in_hours_min=total_busy,
        final_wait_min=total_final,
        mass_g=sum(known) if known and len(known) == len(masses) else None,
        late_jobs=sum(1 for j in job_results if j.late_min > 0),
        unscheduled_jobs=len(schedule.unscheduled),
        printers=tuple(printer_stats),
        jobs=tuple(job_results),
    )
