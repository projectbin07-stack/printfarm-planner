"""Local-search improvement phase on top of a greedy schedule.

A schedule is represented by the plate sequence of each printer; every
candidate is timed by :func:`.scheduler.simulate`, which books plates through
the same :class:`.scheduler.Dispatcher` as the greedy planner (labour windows,
operator capacity, material changes, compatibility). Neighbourhoods:

``move``      take one plate and reinsert it at another position on the same printer
``swap``      exchange two plates of different jobs (same or different printers),
              only when each can run on the other's printer
``reassign``  move one plate to another capable printer, at a random position

Acceptance is hill climbing (accept a candidate that is not worse) or, with
``anneal``, simulated annealing with geometric cooling (see docs/algorithm.md).
The best schedule seen is returned, and the start schedule is the first "best",
so the result is never worse than the start. A run is deterministic for a seed
when it stops on the iteration budget; a wall-clock limit can stop it earlier.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from .fleet import Fleet
from .model import Job, Plate
from .objective import Objective, Weights, evaluate
from .scheduler import Dispatcher, Schedule, sequences_of, simulate

__all__ = ["NEIGHBOURHOODS", "SearchConfig", "SearchResult", "improve"]

NEIGHBOURHOODS = ("move", "swap", "reassign")

Sequences = list[list[Plate]]


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Budget and acceptance rule of the improvement phase."""

    iterations: int = 2000
    #: Wall-clock limit in seconds; ``None`` means only the iteration budget applies.
    time_limit_s: float | None = None
    seed: int = 0
    anneal: bool = False
    #: Starting temperature; ``None`` means 2% of the start objective (at least 1).
    t0: float | None = None
    #: Final temperature as a fraction of ``t0`` (reached at the last iteration).
    t_end_ratio: float = 1e-3

    def __post_init__(self) -> None:
        if self.iterations < 0:
            raise ValueError("iterations must be >= 0")
        if self.time_limit_s is not None and self.time_limit_s <= 0:
            raise ValueError("time limit must be > 0 seconds")
        if self.t0 is not None and self.t0 <= 0:
            raise ValueError("t0 must be > 0")
        if not 0 < self.t_end_ratio <= 1:
            raise ValueError("t_end_ratio must be in (0, 1]")

    @property
    def rule(self) -> str:
        return "anneal" if self.anneal else "hill"


@dataclass(slots=True)
class SearchResult:
    schedule: Schedule
    objective: Objective
    start_objective: Objective
    iterations: int
    accepted: int
    improvements: int
    stopped_by: str  # "iterations", "time" or "no moves"
    seconds: float
    #: neighbourhood -> [tried, accepted]
    moves: dict[str, list[int]] = field(default_factory=dict)


def _neighbour(
    rng: random.Random, current: Sequences, capable: dict[str, frozenset[int]]
) -> tuple[str, Sequences] | None:
    """One random neighbour of ``current``, or ``None`` if the drawn move does not apply."""
    kind = rng.choice(NEIGHBOURHOODS)
    slots = [(i, k) for i, seq in enumerate(current) for k in range(len(seq))]
    if not slots:
        return None
    seqs = [list(seq) for seq in current]
    a, i = rng.choice(slots)
    plate = seqs[a][i]
    if kind == "move":
        if len(seqs[a]) < 2:
            return None
        seqs[a].pop(i)
        j = rng.randrange(len(seqs[a]))
        if j >= i:
            j += 1  # a different position than the one it came from
        seqs[a].insert(j, plate)
        return kind, seqs
    if kind == "swap":
        b, k = rng.choice(slots)
        other = seqs[b][k]
        if other.job.name == plate.job.name:
            return None
        if b not in capable[plate.job.name] or a not in capable[other.job.name]:
            return None
        seqs[a][i], seqs[b][k] = other, plate
        return kind, seqs
    targets = sorted(capable[plate.job.name] - {a})
    if not targets:
        return None
    b = rng.choice(targets)
    seqs[a].pop(i)
    seqs[b].insert(rng.randrange(len(seqs[b]) + 1), plate)
    return kind, seqs


def _has_moves(current: Sequences, capable: dict[str, frozenset[int]]) -> bool:
    plates = [p for seq in current for p in seq]
    return any(len(seq) >= 2 for seq in current) or any(
        len(capable[p.job.name]) > 1 for p in plates
    )


def improve(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    initial: Schedule,
    weights: Weights | None = None,
    config: SearchConfig | None = None,
) -> SearchResult:
    """Improve ``initial`` (a schedule of the same fleet, jobs and start) by local search."""
    weights = weights if weights is not None else Weights()
    config = config if config is not None else SearchConfig()
    clock = time.perf_counter()
    rng = random.Random(config.seed)
    state = Dispatcher(fleet, jobs, start, "search", initial.calendar)
    capable = state.capable
    names = [p.name for p in fleet.printers]
    label = f"{initial.strategy}+{config.rule}"

    start_objective = evaluate(initial, weights)
    best = replace(initial, strategy=label)
    best_objective = start_objective
    by_printer = sequences_of(initial)
    current: Sequences = [list(by_printer[name]) for name in names]
    current_total = start_objective.total
    t0 = config.t0 if config.t0 is not None else max(1.0, 0.02 * abs(start_objective.total))
    moves = {kind: [0, 0] for kind in NEIGHBOURHOODS}
    accepted = improvements = iterations = 0
    stopped_by = "iterations"

    if not _has_moves(current, capable):
        stopped_by = "no moves"
    else:
        while iterations < config.iterations:
            if (
                config.time_limit_s is not None
                and time.perf_counter() - clock >= config.time_limit_s
            ):
                stopped_by = "time"
                break
            iterations += 1
            drawn = _neighbour(rng, current, capable)
            if drawn is None:
                continue
            kind, candidate = drawn
            moves[kind][0] += 1
            schedule = simulate(
                fleet,
                jobs,
                start,
                dict(zip(names, candidate, strict=True)),
                strategy=label,
                calendar=initial.calendar,
            )
            objective = evaluate(schedule, weights)
            delta = objective.total - current_total
            if delta <= 0:
                take = True
            elif config.anneal:
                progress = (iterations - 1) / max(1, config.iterations - 1)
                temperature = t0 * config.t_end_ratio**progress
                take = rng.random() < math.exp(-delta / temperature)
            else:
                take = False
            if take:
                current, current_total = candidate, objective.total
                accepted += 1
                moves[kind][1] += 1
                if objective.better_than(best_objective):
                    best, best_objective = schedule, objective
                    improvements += 1

    return SearchResult(
        schedule=best,
        objective=best_objective,
        start_objective=start_objective,
        iterations=iterations,
        accepted=accepted,
        improvements=improvements,
        stopped_by=stopped_by,
        seconds=time.perf_counter() - clock,
        moves=moves,
    )
