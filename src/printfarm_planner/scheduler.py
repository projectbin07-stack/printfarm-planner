"""Greedy list scheduler for plates on a mixed fleet with operator labour windows.

Model
-----
* Every plate start needs an operator for ``plate_change_min`` (clear the bed,
  start the next file), plus ``material_change_min`` when the printer does not
  already hold every spool the plate needs. That operator task must fit inside
  one labour window, and each operator does one task at a time.
* Printing itself needs nobody, so prints run through nights and weekends; a
  plate that finishes outside a window waits until someone is back.
* After its last plate, each printer that was used gets a **clearing** task of
  ``plate_change_min`` (remove the final plate). It is booked by the same
  dispatch rule as a plate change, so it also needs a labour window and a free
  operator, and the plan ends when the last clearing task ends.
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

Live fleets (since 0.6.0)
-------------------------
Every planner also takes the state the fleet is in when the plan starts:

* ``initial``: a :class:`PrinterState` per printer name. ``available_at_min``
  delays the printer's first operator task (a print still running, or a
  printer that is busy for another reason); ``plate_on_bed`` says a plate will
  be on the bed then, so the printer needs clearing even if the plan gives it
  nothing, and the time that plate waits counts as waiting; ``offline``
  printers get no plates and no tasks.
* ``pinned``: plates per printer name that must be that printer's first
  plates, in the given order (dispatched but not started). The greedy planner
  places them first, :func:`simulate` refuses sequences that do not start with
  them, and neither the local search nor the exact solver moves them.

Both default to an empty fleet with nothing pinned, which reproduces the
behaviour of earlier versions exactly.
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
from .model import Job, Plate, Printer, spool_matches

__all__ = [
    "STRATEGIES",
    "Assignment",
    "Clearing",
    "Dispatcher",
    "PrinterState",
    "Schedule",
    "Unscheduled",
    "plan",
    "sequences_of",
    "simulate",
]

STRATEGIES = ("fill", "edd")


@dataclass(frozen=True, slots=True)
class PrinterState:
    """Where one printer stands when the plan starts. The default is idle with an empty bed.

    For a printer that is printing, set ``available_at_min`` to the predicted end of the
    print and ``plate_on_bed`` to ``True``. For one that has finished and waits to be cleared,
    leave ``available_at_min`` at 0 and set ``plate_on_bed``.
    """

    #: Minutes after the plan start at which the printer is free for its next operator task.
    available_at_min: float = 0.0
    #: A plate is (or will be, at ``available_at_min``) on the bed. The next plate change
    #: removes it as usual; if the plan gives the printer no plate, a clearing task is booked.
    plate_on_bed: bool = False
    #: The printer takes no plates and no operator tasks in this plan.
    offline: bool = False

    def __post_init__(self) -> None:
        if not self.available_at_min >= 0:  # also rejects NaN
            raise ValueError(f"available_at_min must be >= 0, got {self.available_at_min!r}")


_IDLE = PrinterState()


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
class Clearing:
    """The operator task that removes the final plate from a printer."""

    printer: str
    operator: int
    ready: float  # the final plate finished
    start: float
    end: float


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
    #: one per printer that ran a plate or started with one on the bed (empty only in
    #: hand-built schedules)
    clearings: list[Clearing] = field(default_factory=list)
    #: The starting state the plan was made from, by printer name (only non-default entries).
    initial: dict[str, PrinterState] = field(default_factory=dict)
    #: The pinned plates the plan was made with, by printer name.
    pinned: dict[str, tuple[Plate, ...]] = field(default_factory=dict)

    def for_printer(self, name: str) -> list[Assignment]:
        return sorted((a for a in self.assignments if a.printer == name), key=lambda a: a.start)

    def clearing_for(self, name: str) -> Clearing | None:
        return next((c for c in self.clearings if c.printer == name), None)

    def state_of(self, name: str) -> PrinterState:
        """The starting state of printer ``name`` (idle with an empty bed by default)."""
        return self.initial.get(name, _IDLE)


def _needs_change(loaded: tuple[str, ...], spools: tuple[str, ...]) -> bool:
    if not loaded:  # nothing declared: assume the printer is prepared for its first plate
        return False
    return not all(any(spool_matches(have, need) for have in loaded) for need in spools)


def _after_load(loaded: tuple[str, ...], spools: tuple[str, ...], capacity: int) -> tuple[str, ...]:
    """Spools on the printer after loading ``spools``; most recently used first.

    A needed spool already served by a loaded one keeps the loaded label, unless the loaded
    label has no colour and the needed one does (the colour is then known).
    """
    slots = list(loaded)
    used: list[str] = []
    for need in dict.fromkeys(spools):
        idx = next(
            (i for i, h in enumerate(slots) if h not in used and spool_matches(h, need)), None
        )
        if idx is None:
            used.append(need)
            continue
        if ":" in need and ":" not in slots[idx]:
            slots[idx] = need  # the same physical spool; its colour is now known
        used.append(slots[idx])
    merged = list(dict.fromkeys(used)) + [s for s in slots if s not in used]
    return tuple(merged[:capacity])


def _plate_key(plate: Plate) -> tuple[str, int]:
    return (plate.job.name, plate.copy)


class Dispatcher:
    """Places plates on printers one at a time: the shared core of every planner.

    It holds the operator pool, when each printer becomes free and which spools
    each printer holds. The greedy :func:`plan`, :func:`simulate` (fixed plate
    sequences, used by the local search) and the exact solver all book plates
    through :meth:`place` and final-plate clearing through :meth:`clear`, so
    labour windows, operator capacity, material changes, compatibility, the
    starting state and pinned plates are enforced by the same code everywhere.
    """

    def __init__(
        self,
        fleet: Fleet,
        jobs: Sequence[Job],
        start: datetime,
        strategy: str,
        calendar: Calendar | None = None,
        *,
        initial: Mapping[str, PrinterState] | None = None,
        pinned: Mapping[str, Sequence[Plate]] | None = None,
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
        index_of = {p.name: i for i, p in enumerate(self.printers)}
        initial = dict(initial or {})
        unknown = sorted(set(initial) - set(index_of))
        if unknown:
            raise ValueError(f"unknown printer(s) in initial state: {', '.join(unknown)}")
        self.states = [initial.get(p.name, _IDLE) for p in self.printers]
        self.schedule.initial = {n: s for n, s in initial.items() if s != _IDLE}
        #: job name -> indexes of the printers that can run it
        self.capable: dict[str, frozenset[int]] = {}
        #: every plate that at least one printer can run, in input order
        self.plates: list[Plate] = []
        for job in jobs:
            reasons = {p.name: incompatibilities(p, job) for p in self.printers}
            for printer, state in zip(self.printers, self.states, strict=True):
                if state.offline:
                    reasons[printer.name] = [*reasons[printer.name], "printer is offline"]
            ok = frozenset(i for i, p in enumerate(self.printers) if not reasons[p.name])
            if not ok:
                self.schedule.unscheduled.append(Unscheduled(job, reasons))
                continue
            self.capable[job.name] = ok
            self.plates.extend(Plate(job, number) for number in range(1, job.copies + 1))
        self.ready = [s.available_at_min for s in self.states]
        self.loaded = [p.loaded for p in self.printers]
        #: printer holds a plate that must be cleared (it ran one, or started with one on the
        #: bed) / its final plate has been cleared
        self.used = [s.plate_on_bed and not s.offline for s in self.states]
        self.cleared = [False] * len(self.printers)
        #: plates that must be each printer's next plates, in order
        self.pinned: list[deque[Plate]] = [deque() for _ in self.printers]
        self._pinned_to: dict[tuple[str, int], int] = {}
        schedulable = {_plate_key(p): p for p in self.plates}
        for name, plates in (pinned or {}).items():
            if name not in index_of:
                raise ValueError(f"unknown printer {name!r} in pinned plates")
            index = index_of[name]
            for plate in plates:
                key = _plate_key(plate)
                if key not in schedulable:
                    raise ValueError(f"pinned plate {plate.label} is not a schedulable plate")
                if key in self._pinned_to:
                    raise ValueError(f"plate {plate.label} is pinned twice")
                if index not in self.capable[plate.job.name]:
                    raise ValueError(f"pinned plate {plate.label} cannot run on {name}")
                self._pinned_to[key] = index
                self.pinned[index].append(schedulable[key])
        self.schedule.pinned = {
            p.name: tuple(self.pinned[i]) for i, p in enumerate(self.printers) if self.pinned[i]
        }

    def is_pinned(self, plate: Plate) -> bool:
        """``plate`` is pinned to a printer (whether or not it has been placed yet)."""
        return _plate_key(plate) in self._pinned_to

    def free_plates(self) -> list[Plate]:
        """Schedulable plates that are not pinned, in input order."""
        return [p for p in self.plates if not self.is_pinned(p)]

    def slot(self, index: int) -> float:
        """Earliest time an operator could start a plate change or clearing on ``index``."""
        return self.pool.earliest(self.ready[index], self.labour.plate_change_min)[0]

    def needs_change(self, index: int, job: Job) -> bool:
        return _needs_change(self.loaded[index], job.spools)

    def place(self, index: int, plate: Plate) -> Assignment:
        """Book ``plate`` as the next plate on printer ``index`` and return the assignment."""
        printer = self.printers[index]
        job = plate.job
        if index not in self.capable.get(job.name, frozenset()):
            raise ValueError(f"{plate.label} cannot run on {printer.name}")
        if self.cleared[index]:
            raise ValueError(f"{printer.name} is already cleared and takes no more plates")
        queue = self.pinned[index]
        if queue and _plate_key(queue[0]) != _plate_key(plate):
            raise ValueError(f"{printer.name} must run its pinned plate {queue[0].label} next")
        pinned_to = self._pinned_to.get(_plate_key(plate))
        if pinned_to is not None and pinned_to != index:
            raise ValueError(f"{plate.label} is pinned to {self.printers[pinned_to].name}")
        if queue:
            queue.popleft()
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
        self.used[index] = True
        return assignment

    def clear(self, index: int) -> Clearing:
        """Book removing the final plate from printer ``index``; it takes no more plates.

        The task lasts ``plate_change_min`` and is booked like a plate change:
        inside one labour window, with a free operator.
        """
        printer = self.printers[index]
        if not self.used[index] or self.cleared[index]:
            raise ValueError(f"{printer.name} has no finished plate to clear")
        if self.pinned[index]:
            raise ValueError(f"{printer.name} still has pinned plates to run")
        duration = self.labour.plate_change_min
        begin, operator = self.pool.earliest(self.ready[index], duration)
        self.pool.book(operator, begin, duration)
        clearing = Clearing(
            printer=printer.name,
            operator=operator,
            ready=self.ready[index],
            start=begin,
            end=begin + duration,
        )
        self.schedule.clearings.append(clearing)
        self.cleared[index] = True
        return clearing

    def awaiting_clear(self, index: int) -> bool:
        """``index`` holds a plate (run here or there at the start) not cleared yet."""
        return self.used[index] and not self.cleared[index]

    def clear_all(self) -> None:
        """Clear every printer still holding a final plate, in dispatch order."""
        while pending := [i for i in range(len(self.printers)) if self.awaiting_clear(i)]:
            self.clear(min(pending, key=lambda i: (self.slot(i), i)))

    def copy(self) -> Dispatcher:
        """An independent copy of the booking state (the calendar is shared)."""
        other = copy.copy(self)
        other.pool = self.pool.copy()
        other.ready = list(self.ready)
        other.loaded = list(self.loaded)
        other.used = list(self.used)
        other.cleared = list(self.cleared)
        other.pinned = [deque(q) for q in self.pinned]
        other.schedule = replace(
            self.schedule,
            assignments=list(self.schedule.assignments),
            unscheduled=list(self.schedule.unscheduled),
            clearings=list(self.schedule.clearings),
        )
        return other


def plan(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    strategy: str = "fill",
    calendar: Calendar | None = None,
    *,
    initial: Mapping[str, PrinterState] | None = None,
    pinned: Mapping[str, Sequence[Plate]] | None = None,
) -> Schedule:
    """Schedule every plate of ``jobs`` on ``fleet`` starting at ``start``.

    ``calendar`` may be passed to reuse an already generated labour calendar for
    the same start and windows; it only saves time. ``initial`` and ``pinned``
    describe a live fleet (see the module documentation); pinned plates are
    placed first on their printer, in order, and the strategy only orders the rest.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    state = Dispatcher(fleet, jobs, start, strategy, calendar, initial=initial, pinned=pinned)
    printers = state.printers
    capable = state.capable
    cal = state.schedule.calendar
    remaining = state.free_plates()
    order = {id(plate): index for index, plate in enumerate(remaining)}
    change = state.labour.plate_change_min
    swap = state.labour.material_change_min

    while remaining or any(state.pinned):
        # Candidates: printers with pinned plates left, printers that can still take a
        # plate, and used printers that cannot and wait for their final plate to be
        # cleared. The printer whose next operator slot comes first is served.
        best: tuple[float, int] | None = None
        for index in range(len(printers)):
            can_run = bool(state.pinned[index]) or any(
                index in capable[p.job.name] for p in remaining
            )
            if not can_run and not state.awaiting_clear(index):
                continue
            slot = state.slot(index)
            if best is None or (slot, index) < best:
                best = (slot, index)
        if best is None:  # pragma: no cover - every remaining plate has a capable printer
            break
        slot, index = best
        if state.pinned[index]:
            state.place(index, state.pinned[index][0])
            continue
        if not any(index in capable[p.job.name] for p in remaining):
            state.clear(index)
            continue

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
    state.clear_all()
    return state.schedule


def sequences_of(schedule: Schedule) -> dict[str, list[Plate]]:
    """The plates on each printer of ``schedule``, in start order."""
    return {p.name: [a.plate for a in schedule.for_printer(p.name)] for p in schedule.printers}


def simulate(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    sequences: Mapping[str, Sequence[Plate]],
    strategy: str = "sequence",
    calendar: Calendar | None = None,
    *,
    initial: Mapping[str, PrinterState] | None = None,
    pinned: Mapping[str, Sequence[Plate]] | None = None,
) -> Schedule:
    """Time a schedule from a fixed plate order per printer.

    Plates are booked with the same dispatch rule as :func:`plan`: the printer
    whose next operator slot comes first gets the operator next (ties go to the
    printer listed first in the fleet). A printer whose sequence is used up
    competes in the same way for the clearing of its final plate. Every plate
    that some printer can run must appear exactly once, and each printer's
    sequence must start with its ``pinned`` plates in order. Passing
    ``sequences_of(plan(...))`` with the same ``initial`` and ``pinned``
    reproduces the greedy schedule exactly.
    """
    state = Dispatcher(fleet, jobs, start, strategy, calendar, initial=initial, pinned=pinned)
    index_of = {p.name: i for i, p in enumerate(state.printers)}
    unknown = sorted(set(sequences) - set(index_of))
    if unknown:
        raise ValueError(f"unknown printer(s) in sequences: {', '.join(unknown)}")
    given = sorted(_plate_key(p) for seq in sequences.values() for p in seq)
    expected = sorted(_plate_key(p) for p in state.plates)
    if given != expected:
        raise ValueError("sequences must list every schedulable plate exactly once")
    for index, queue in enumerate(state.pinned):
        name = state.printers[index].name
        head = [_plate_key(p) for p in list(sequences.get(name, ()))[: len(queue)]]
        if head != [_plate_key(p) for p in queue]:
            raise ValueError(f"the sequence of {name} must start with its pinned plates")
    queues: list[deque[Plate]] = [deque() for _ in state.printers]
    for name, seq in sequences.items():
        queues[index_of[name]].extend(seq)
    while True:
        best: tuple[float, int] | None = None
        for index, queue in enumerate(queues):
            if queue or state.awaiting_clear(index):
                slot = state.slot(index)
                if best is None or (slot, index) < best:
                    best = (slot, index)
        if best is None:
            return state.schedule
        index = best[1]
        if queues[index]:
            state.place(index, queues[index].popleft())
        else:
            state.clear(index)
