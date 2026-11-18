"""Greedy list scheduler for plates on a mixed fleet with operator labour windows.

Model
-----
* Every plate start needs an operator for ``plate_change_min`` (clear the bed,
  start the next file), plus ``material_change_min`` when the printer does not
  already hold every spool the plate needs. That operator task must fit inside
  one labour window, and each operator does one task at a time.
* Printing itself needs nobody, so prints run through nights and weekends; a
  plate that finishes outside a window waits until someone is back.
* The scheduler repeatedly takes the printer whose next operator slot comes
  first and picks a plate for it with the chosen strategy:

  ``fill``  least time the finished plate would sit waiting for an operator,
            then fewest material changes, then earliest due date, then longest;
  ``edd``   earliest due date, then fewest material changes, then longest.

  Both honour ``priority`` (higher first) before anything else, and prefer
  plates that fewer printers can run. This is a heuristic: it is fast and
  explainable, not optimal.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .compat import incompatibilities
from .fleet import Fleet, Labour
from .labour import Calendar, OperatorPool
from .model import Job, Plate, Printer

__all__ = ["STRATEGIES", "Assignment", "Schedule", "Unscheduled", "plan"]

STRATEGIES = ("fill", "edd")


@dataclass(frozen=True, slots=True)
class Assignment:
    """One plate placed on one printer. Times are minutes from the plan start."""

    printer: str
    plate: Plate
    operator: int
    ready: float  # printer became free (previous plate finished, or plan start)
    setup_start: float
    start: float
    end: float
    material_change: bool
    loaded: tuple[str, ...]  # spools on the printer after the setup

    @property
    def setup_min(self) -> float:
        return self.start - self.setup_start

    @property
    def print_min(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Unscheduled:
    job: Job
    reasons: dict[str, list[str]]  # printer name -> why it cannot run the job


@dataclass(slots=True)
class Schedule:
    start: datetime
    strategy: str
    printers: tuple[Printer, ...]
    labour: Labour
    calendar: Calendar
    jobs: tuple[Job, ...]
    assignments: list[Assignment] = field(default_factory=list)
    unscheduled: list[Unscheduled] = field(default_factory=list)

    def for_printer(self, name: str) -> list[Assignment]:
        return sorted((a for a in self.assignments if a.printer == name), key=lambda a: a.start)


def _needs_change(loaded: tuple[str, ...], spools: tuple[str, ...]) -> bool:
    if not loaded:  # nothing declared: assume the printer is prepared for its first plate
        return False
    return not set(spools) <= set(loaded)


def _after_load(loaded: tuple[str, ...], spools: tuple[str, ...], capacity: int) -> tuple[str, ...]:
    """Spools on the printer after loading ``spools``; most recently used first."""
    merged = list(dict.fromkeys(spools))
    merged += [s for s in loaded if s not in merged]
    return tuple(merged[:capacity])


def plan(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    strategy: str = "fill",
) -> Schedule:
    """Schedule every plate of ``jobs`` on ``fleet`` starting at ``start``."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    names = [job.name for job in jobs]
    if len(set(names)) != len(names):
        raise ValueError("job names must be unique")

    labour = fleet.labour
    printers = fleet.printers
    calendar = Calendar(start, labour.weekly)
    pool = OperatorPool(calendar, labour.operators)
    schedule = Schedule(
        start=start,
        strategy=strategy,
        printers=printers,
        labour=labour,
        calendar=calendar,
        jobs=tuple(jobs),
    )

    capable: dict[str, frozenset[int]] = {}
    remaining: list[Plate] = []
    for job in jobs:
        reasons = {p.name: incompatibilities(p, job) for p in printers}
        ok = frozenset(i for i, p in enumerate(printers) if not reasons[p.name])
        if not ok:
            schedule.unscheduled.append(Unscheduled(job, reasons))
            continue
        capable[job.name] = ok
        remaining.extend(Plate(job, copy) for copy in range(1, job.copies + 1))

    order = {id(plate): index for index, plate in enumerate(remaining)}
    ready = [0.0] * len(printers)
    loaded = [p.loaded for p in printers]
    change = labour.plate_change_min
    swap = labour.material_change_min

    while remaining:
        best: tuple[float, int] | None = None
        for index in range(len(printers)):
            if not any(index in capable[p.job.name] for p in remaining):
                continue
            slot, _ = pool.earliest(ready[index], change)
            if best is None or (slot, index) < best:
                best = (slot, index)
        if best is None:  # pragma: no cover - every remaining plate has a capable printer
            break
        slot, index = best
        printer = printers[index]

        def key(plate: Plate, slot: float = slot, index: int = index) -> tuple[float, ...]:
            job = plate.job
            needs = _needs_change(loaded[index], job.spools)
            begin = slot + change + (swap if needs else 0.0)
            end = begin + job.print_time_min * printers[index].time_factor
            waiting = calendar.earliest_fit(end, change) - end
            due = job.due_min if job.due_min is not None else math.inf
            tail = (-job.print_time_min, len(capable[job.name]), order[id(plate)])
            if strategy == "edd":
                return (-job.priority, due, float(needs), *tail)
            return (-job.priority, round(waiting, 6), float(needs), due, *tail)

        plate = min((p for p in remaining if index in capable[p.job.name]), key=key)
        job = plate.job
        needs = _needs_change(loaded[index], job.spools)
        setup = change + (swap if needs else 0.0)
        setup_start, operator = pool.earliest(ready[index], setup)
        pool.book(operator, setup_start, setup)
        begin = setup_start + setup
        end = begin + job.print_time_min * printer.time_factor
        loaded[index] = _after_load(loaded[index], job.spools, printer.spool_capacity)
        schedule.assignments.append(
            Assignment(
                printer=printer.name,
                plate=plate,
                operator=operator,
                ready=ready[index],
                setup_start=setup_start,
                start=begin,
                end=end,
                material_change=needs,
                loaded=loaded[index],
            )
        )
        ready[index] = end
        remaining.remove(plate)
    return schedule
