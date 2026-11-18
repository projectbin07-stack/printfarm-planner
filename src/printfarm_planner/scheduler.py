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

All placement goes through :class:`Dispatcher`, which the local search
(:mod:`.search`) and the exact solver (:mod:`.exact`) reuse, so every planner
obeys the same labour, operator and compatibility rules.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from .compat import incompatibilities
from .fleet import Fleet, Labour
from .labour import Calendar, OperatorPool
from .model import Job, Plate, Printer

__all__ = [
    "STRATEGIES",
    "Assignment",
    "Dispatcher",
    "Schedule",
    "Unscheduled",
    "plan",
    "sequences_of",
    "simulate",
]

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


class Dispatcher:
    """Places plates on printers one at a time: the shared core of every planner.

    It holds the operator pool, when each printer becomes free and which spools
    each printer holds. The greedy :func:`plan`, :func:`simulate` (fixed plate
    sequences, used by the local search) and the exact solver all book plates
    through :meth:`place`, so labour windows, operator capacity, material
    changes and compatibility are enforced by the same code everywhere.
    """

    def __init__(
        self,
        fleet: Fleet,
        jobs: Sequence[Job],
        start: datetime,
        strategy: str,
        calendar: Calendar | None = None,
    ) -> None:
        names = [job.name for job in jobs]
        if len(set(names)) != len(names):
            raise ValueError("job names must be unique")
        self.printers = fleet.printers
        self.labour = fleet.labour
        if calendar is None or calendar.start != start or calendar.weekly != self.labour.weekly:
            calendar = Calendar(start, self.labour.weekly)
        self.pool = OperatorPool(calendar, self.labour.operators)
        self.schedule = Schedule(
            start=start,
            strategy=strategy,
            printers=self.printers,
            labour=self.labour,
            calendar=calendar,
            jobs=tuple(jobs),
        )
        #: job name -> indexes of the printers that can run it
        self.capable: dict[str, frozenset[int]] = {}
        #: every plate that at least one printer can run, in input order
        self.plates: list[Plate] = []
        for job in jobs:
            reasons = {p.name: incompatibilities(p, job) for p in self.printers}
            ok = frozenset(i for i, p in enumerate(self.printers) if not reasons[p.name])
            if not ok:
                self.schedule.unscheduled.append(Unscheduled(job, reasons))
                continue
            self.capable[job.name] = ok
            self.plates.extend(Plate(job, number) for number in range(1, job.copies + 1))
        self.ready = [0.0] * len(self.printers)
        self.loaded = [p.loaded for p in self.printers]

    def slot(self, index: int) -> float:
        """Earliest time an operator could start a plate change on printer ``index``."""
        return self.pool.earliest(self.ready[index], self.labour.plate_change_min)[0]

    def needs_change(self, index: int, job: Job) -> bool:
        return _needs_change(self.loaded[index], job.spools)

    def place(self, index: int, plate: Plate) -> Assignment:
        """Book ``plate`` as the next plate on printer ``index`` and return the assignment."""
        printer = self.printers[index]
        job = plate.job
        if index not in self.capable.get(job.name, frozenset()):
            raise ValueError(f"{plate.label} cannot run on {printer.name}")
        needs = _needs_change(self.loaded[index], job.spools)
        setup = self.labour.plate_change_min + (self.labour.material_change_min if needs else 0.0)
        setup_start, operator = self.pool.earliest(self.ready[index], setup)
        self.pool.book(operator, setup_start, setup)
        begin = setup_start + setup
        end = begin + job.print_time_min * printer.time_factor
        self.loaded[index] = _after_load(self.loaded[index], job.spools, printer.spool_capacity)
        assignment = Assignment(
            printer=printer.name,
            plate=plate,
            operator=operator,
            ready=self.ready[index],
            setup_start=setup_start,
            start=begin,
            end=end,
            material_change=needs,
            loaded=self.loaded[index],
        )
        self.schedule.assignments.append(assignment)
        self.ready[index] = end
        return assignment

    def copy(self) -> Dispatcher:
        """An independent copy of the booking state (the calendar is shared)."""
        other = copy.copy(self)
        other.pool = self.pool.copy()
        other.ready = list(self.ready)
        other.loaded = list(self.loaded)
        other.schedule = replace(
            self.schedule,
            assignments=list(self.schedule.assignments),
            unscheduled=list(self.schedule.unscheduled),
        )
        return other


def plan(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    strategy: str = "fill",
    calendar: Calendar | None = None,
) -> Schedule:
    """Schedule every plate of ``jobs`` on ``fleet`` starting at ``start``.

    ``calendar`` may be passed to reuse an already generated labour calendar for
    the same start and windows; it only saves time.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    state = Dispatcher(fleet, jobs, start, strategy, calendar)
    printers = state.printers
    capable = state.capable
    cal = state.schedule.calendar
    remaining = list(state.plates)
    order = {id(plate): index for index, plate in enumerate(remaining)}
    change = state.labour.plate_change_min
    swap = state.labour.material_change_min

    while remaining:
        best: tuple[float, int] | None = None
        for index in range(len(printers)):
            if not any(index in capable[p.job.name] for p in remaining):
                continue
            slot = state.slot(index)
            if best is None or (slot, index) < best:
                best = (slot, index)
        if best is None:  # pragma: no cover - every remaining plate has a capable printer
            break
        slot, index = best

        def key(plate: Plate, slot: float = slot, index: int = index) -> tuple[float, ...]:
            job = plate.job
            needs = state.needs_change(index, job)
            begin = slot + change + (swap if needs else 0.0)
            end = begin + job.print_time_min * printers[index].time_factor
            waiting = cal.earliest_fit(end, change) - end
            due = job.due_min if job.due_min is not None else math.inf
            tail = (-job.print_time_min, len(capable[job.name]), order[id(plate)])
            if strategy == "edd":
                return (-job.priority, due, float(needs), *tail)
            return (-job.priority, round(waiting, 6), float(needs), due, *tail)

        plate = min((p for p in remaining if index in capable[p.job.name]), key=key)
        state.place(index, plate)
        remaining.remove(plate)
    return state.schedule


def sequences_of(schedule: Schedule) -> dict[str, list[Plate]]:
    """The plates on each printer of ``schedule``, in start order."""
    return {p.name: [a.plate for a in schedule.for_printer(p.name)] for p in schedule.printers}


def _plate_key(plate: Plate) -> tuple[str, int]:
    return (plate.job.name, plate.copy)


def simulate(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    sequences: Mapping[str, Sequence[Plate]],
    strategy: str = "sequence",
    calendar: Calendar | None = None,
) -> Schedule:
    """Time a schedule from a fixed plate order per printer.

    Plates are booked with the same dispatch rule as :func:`plan`: the printer
    whose next operator slot comes first gets the operator next (ties go to the
    printer listed first in the fleet). Every plate that some printer can run
    must appear exactly once. Passing ``sequences_of(plan(...))`` reproduces the
    greedy schedule exactly.
    """
    state = Dispatcher(fleet, jobs, start, strategy, calendar)
    index_of = {p.name: i for i, p in enumerate(state.printers)}
    unknown = sorted(set(sequences) - set(index_of))
    if unknown:
        raise ValueError(f"unknown printer(s) in sequences: {', '.join(unknown)}")
    given = sorted(_plate_key(p) for seq in sequences.values() for p in seq)
    expected = sorted(_plate_key(p) for p in state.plates)
    if given != expected:
        raise ValueError("sequences must list every schedulable plate exactly once")
    queues: list[deque[Plate]] = [deque() for _ in state.printers]
    for name, seq in sequences.items():
        queues[index_of[name]].extend(seq)
    while True:
        best: tuple[float, int] | None = None
        for index, queue in enumerate(queues):
            if queue:
                slot = state.slot(index)
                if best is None or (slot, index) < best:
                    best = (slot, index)
        if best is None:
            return state.schedule
        index = best[1]
        state.place(index, queues[index].popleft())
