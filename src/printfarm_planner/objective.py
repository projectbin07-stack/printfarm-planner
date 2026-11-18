"""The scheduling objective: a weighted sum of terms that are also reported one by one.

    J = a_T * sum_j w_j * max(0, C_j - d_j)      weighted tardiness (minutes)
      + a_C * C_max                              makespan (minutes)
      + a_off * W_off                            finished plates waiting outside labour windows
      + a_in * W_in                              finished plates waiting inside labour windows
      + a_M * N_M                                material changes (count)

``w_j = max(1, 1 + priority_j)``, ``C_j`` is when the last plate of job ``j``
finishes and ``d_j`` its due time; jobs without a due date add nothing. The
waiting terms are the figures from :mod:`.metrics` (gaps between consecutive
plates on a printer, split by the labour calendar). Lower is better. See
``docs/algorithm.md`` for the reasoning behind the default weights.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from .metrics import Metrics, compute_metrics
from .model import Job
from .scheduler import Schedule

__all__ = ["TERMS", "Objective", "Weights", "evaluate", "job_weight"]

#: Objective terms in report order.
TERMS = ("tardiness", "makespan", "wait_off", "wait_in", "material_changes")

_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class Weights:
    """Multipliers ``a_*`` of the objective terms. All must be >= 0."""

    tardiness: float = 1.0
    makespan: float = 1.0
    wait_off: float = 0.2
    wait_in: float = 0.5
    material_changes: float = 10.0

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if not value >= 0:  # also rejects NaN
                raise ValueError(f"weight {f.name} must be >= 0, got {value!r}")

    @classmethod
    def parse(cls, items: Iterable[str]) -> Weights:
        """Defaults overridden by ``"name=value"`` items, e.g. ``["makespan=0.5"]``."""
        weights = cls()
        for item in items:
            name, sep, value = item.partition("=")
            name = name.strip().replace("-", "_")
            if not sep or name not in TERMS:
                raise ValueError(f"bad weight {item!r}; use NAME=VALUE with NAME one of {TERMS}")
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"bad weight {item!r}: {value!r} is not a number") from None
            weights = replace(weights, **{name: number})
        return weights

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Objective:
    """Raw term values (before weighting) and the weights used."""

    weights: Weights
    tardiness: float  # sum of w_j * minutes late
    makespan: float
    wait_off: float
    wait_in: float
    material_changes: int

    def contributions(self) -> dict[str, float]:
        """Weight times value, per term."""
        return {name: getattr(self.weights, name) * float(getattr(self, name)) for name in TERMS}

    @property
    def total(self) -> float:
        return sum(self.contributions().values())

    def better_than(self, other: Objective) -> bool:
        return self.total < other.total - _TOLERANCE * max(1.0, abs(other.total))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 3),
            "terms": {name: round(float(getattr(self, name)), 3) for name in TERMS},
            "weights": self.weights.to_dict(),
            "contributions": {k: round(v, 3) for k, v in self.contributions().items()},
        }


def job_weight(job: Job) -> float:
    """``w_j`` in the tardiness term: 1 for priority 0 or lower, ``1 + priority`` above."""
    return float(max(1, 1 + job.priority))


def evaluate(
    schedule: Schedule, weights: Weights | None = None, metrics: Metrics | None = None
) -> Objective:
    """Objective of ``schedule``. Pass ``metrics`` if already computed."""
    weights = weights if weights is not None else Weights()
    metrics = metrics if metrics is not None else compute_metrics(schedule)
    by_name = {job.name: job for job in schedule.jobs}
    tardiness = sum(job_weight(by_name[j.name]) * j.late_min for j in metrics.jobs)
    return Objective(
        weights=weights,
        tardiness=tardiness,
        makespan=metrics.makespan_min,
        wait_off=metrics.wait_off_hours_min,
        wait_in=metrics.wait_in_hours_min,
        material_changes=metrics.material_changes,
    )
