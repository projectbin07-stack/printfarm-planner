"""Exact branch-and-bound over plate-to-printer assignment and order, for small instances.

Search space
------------
Every schedule the greedy and the local search can produce is a plate sequence
per printer, timed with the shared dispatch rule (the printer whose next
operator slot comes first is served next; see :func:`.scheduler.simulate`).
This solver enumerates that space exhaustively, so its result is optimal
*within it*. It does not insert deliberate idle time or let an operator serve
printers in another order, so it is not a proof of optimality for every
conceivable timetable.

The tree follows the dispatch rule: at each node the open printer with the
earliest operator slot either gets one of the remaining plates it can run, or
is closed (it gets no more plates). Copies of one job are interchangeable, so
only the lowest remaining copy number is branched on. A node is pruned when
its lower bound (below) is not better than the best complete schedule found;
the search starts from an upper bound such as the greedy or improved schedule.

Lower bound of a partial schedule: the material changes and waiting already
incurred (they never decrease), makespan at least the latest end so far and,
for every remaining plate, at least its earliest possible finish on an open
capable printer (next labour slot after that printer is free, plus plate
change and print time, ignoring operator contention and material changes);
the same per-job finish bound gives a tardiness bound.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from .fleet import Fleet
from .model import Job, Plate
from .objective import Objective, Weights, evaluate, job_weight
from .scheduler import Dispatcher, Schedule

__all__ = ["EXACT_PLATE_LIMIT", "ExactResult", "solve_exact"]

#: Default largest instance (number of schedulable plates) the exact solver accepts.
EXACT_PLATE_LIMIT = 8

_EPS = 1e-9


@dataclass(slots=True)
class ExactResult:
    schedule: Schedule | None
    objective: Objective | None
    #: True when the whole tree was searched, so ``schedule`` is optimal in the model.
    optimal: bool
    nodes: int
    seconds: float
    #: Why no search was done, or why it stopped early; empty when it completed.
    note: str = ""


class _Search:
    def __init__(
        self,
        root: Dispatcher,
        weights: Weights,
        node_limit: int,
        best: Schedule | None,
    ) -> None:
        self.weights = weights
        self.node_limit = node_limit
        self.nodes = 0
        self.complete = True
        self.calendar = root.schedule.calendar
        self.change = root.labour.plate_change_min
        self.best = best
        self.best_total = evaluate(best, weights).total if best is not None else float("inf")
        self.jobs = {job.name: job for job in root.schedule.jobs}

    # -- bound -------------------------------------------------------------
    def bound(
        self,
        state: Dispatcher,
        remaining: dict[str, int],
        open_: list[bool],
        incurred: float,
    ) -> float:
        """Lower bound on the objective of any completion of ``state``."""
        w = self.weights
        makespan = max(state.ready, default=0.0)
        finish: dict[str, float] = {}
        for a in state.schedule.assignments:
            name = a.plate.job.name
            finish[name] = max(finish.get(name, 0.0), a.end)
        for name, count in remaining.items():
            if not count:
                continue
            job = self.jobs[name]
            earliest = float("inf")
            for index in state.capable[name]:
                if open_[index]:
                    slot = self.calendar.earliest_fit(state.ready[index], self.change)
                    end = (
                        slot + self.change + job.print_time_min * state.printers[index].time_factor
                    )
                    earliest = min(earliest, end)
            if earliest == float("inf"):
                return float("inf")  # a remaining plate has no open printer left
            finish[name] = max(finish.get(name, 0.0), earliest)
            makespan = max(makespan, earliest)
        tardiness = 0.0
        for name, end in finish.items():
            job = self.jobs[name]
            if job.due_min is not None:
                tardiness += job_weight(job) * max(0.0, end - job.due_min)
        return incurred + w.makespan * makespan + w.tardiness * tardiness

    def _gap_cost(self, prev_end: float, setup_start: float) -> float:
        if setup_start <= prev_end:
            return 0.0
        inside = sum(hi - lo for lo, hi in self.calendar.windows_between(prev_end, setup_start))
        outside = (setup_start - prev_end) - inside
        return self.weights.wait_in * inside + self.weights.wait_off * outside

    # -- search ------------------------------------------------------------
    def run(
        self,
        state: Dispatcher,
        remaining: dict[str, int],
        open_: list[bool],
        used: list[bool],
        incurred: float,
    ) -> None:
        if self.nodes >= self.node_limit:
            self.complete = False
            return
        self.nodes += 1
        if not any(remaining.values()):
            schedule = state.schedule
            total = evaluate(schedule, self.weights).total
            if total < self.best_total - _EPS * max(1.0, abs(total)):
                self.best, self.best_total = schedule, total
            return

        # The open printer that the dispatch rule serves next. Printers that cannot
        # run any remaining plate are closed on the way (they would get nothing).
        index = -1
        best_slot = float("inf")
        for i, is_open in enumerate(open_):
            if not is_open:
                continue
            if not any(count and i in state.capable[name] for name, count in remaining.items()):
                continue
            slot = state.slot(i)
            if slot < best_slot:
                index, best_slot = i, slot
        if index < 0:
            return  # plates remain but no open printer can run them

        children: list[tuple[float, int, str, Dispatcher, float]] = []
        for order, (name, count) in enumerate(remaining.items()):
            if not count or index not in state.capable[name]:
                continue
            job = self.jobs[name]
            child = state.copy()
            assignment = child.place(index, Plate(job, job.copies - count + 1))
            cost = incurred + self.weights.material_changes * assignment.material_change
            if used[index]:
                cost += self._gap_cost(assignment.ready, assignment.setup_start)
            after = dict(remaining)
            after[name] = count - 1
            lower = self.bound(child, after, open_, cost)
            children.append((lower, order, name, child, cost))
        children.sort(key=lambda c: (c[0], c[1]))
        for lower, _, name, child, cost in children:
            if lower >= self.best_total - _EPS * max(1.0, abs(self.best_total)):
                break  # sorted by bound: the rest cannot be better either
            after = dict(remaining)
            after[name] -= 1
            now_used = list(used)
            now_used[index] = True
            self.run(child, after, open_, now_used, cost)

        closed = list(open_)
        closed[index] = False
        if self.bound(state, remaining, closed, incurred) < self.best_total - _EPS * max(
            1.0, abs(self.best_total)
        ):
            self.run(state, remaining, closed, used, incurred)


def solve_exact(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    weights: Weights | None = None,
    upper_bound: Schedule | None = None,
    max_plates: int = EXACT_PLATE_LIMIT,
    node_limit: int = 2_000_000,
) -> ExactResult:
    """Best plate sequences for ``jobs`` under ``weights``, if at most ``max_plates`` plates.

    ``upper_bound`` (for example the greedy or improved schedule of the same
    input) seeds the search; it is returned unchanged if nothing better exists.
    """
    weights = weights if weights is not None else Weights()
    clock = time.perf_counter()
    calendar = upper_bound.calendar if upper_bound is not None else None
    root = Dispatcher(fleet, jobs, start, "exact", calendar)
    count = len(root.plates)
    if count > max_plates:
        return ExactResult(
            schedule=None,
            objective=None,
            optimal=False,
            nodes=0,
            seconds=time.perf_counter() - clock,
            note=f"{count} plates is above the exact-solver limit of {max_plates}",
        )
    remaining = {job.name: job.copies for job in jobs if job.name in root.capable}
    search = _Search(root, weights, node_limit, upper_bound)
    n = len(root.printers)
    search.run(root, remaining, [True] * n, [False] * n, 0.0)
    best = search.best
    if best is None:  # no upper bound given and nothing to place
        best = root.schedule
    best = replace(best, strategy="exact")
    return ExactResult(
        schedule=best,
        objective=evaluate(best, weights),
        optimal=search.complete,
        nodes=search.nodes,
        seconds=time.perf_counter() - clock,
        note="" if search.complete else f"node limit {node_limit} reached; best found so far",
    )
