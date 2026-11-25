"""Run every planner on the same input and compare objective terms and run times."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape
from typing import Any

from . import __version__
from .exact import EXACT_PLATE_LIMIT, solve_exact
from .fleet import Fleet
from .gantt import CSS, chart, legend, objective_table
from .metrics import compute_metrics
from .model import Job, Plate
from .objective import Objective, Weights, evaluate
from .report import format_duration, text_table
from .scheduler import STRATEGIES, PrinterState, Schedule, plan
from .search import SearchConfig, improve

__all__ = ["Run", "compare", "compare_json", "format_compare", "render_compare_html"]


@dataclass(slots=True)
class Run:
    method: str
    schedule: Schedule | None
    objective: Objective | None
    seconds: float
    note: str = ""


def compare(
    fleet: Fleet,
    jobs: Sequence[Job],
    start: datetime,
    weights: Weights | None = None,
    config: SearchConfig | None = None,
    exact_limit: int = EXACT_PLATE_LIMIT,
    node_limit: int = 2_000_000,
    *,
    initial: Mapping[str, PrinterState] | None = None,
    pinned: Mapping[str, Sequence[Plate]] | None = None,
) -> list[Run]:
    """``fill``, ``edd``, hill climbing, annealing and (if small enough) the exact solver.

    Both local searches start from whichever greedy schedule has the lower
    objective (``fill`` on a tie). The exact solver is seeded with the best
    schedule found by the others. ``initial`` and ``pinned`` are passed to every
    planner (see :mod:`.scheduler`).
    """
    weights = weights if weights is not None else Weights()
    config = config if config is not None else SearchConfig()
    runs: list[Run] = []
    calendar = None
    for strategy in STRATEGIES:
        clock = time.perf_counter()
        schedule = plan(fleet, jobs, start, strategy, calendar, initial=initial, pinned=pinned)
        objective = evaluate(schedule, weights)
        runs.append(Run(strategy, schedule, objective, time.perf_counter() - clock))
        calendar = schedule.calendar

    def total(run: Run) -> float:
        return run.objective.total if run.objective is not None else float("inf")

    base = min(runs, key=total)
    assert base.schedule is not None
    for anneal in (False, True):
        result = improve(fleet, jobs, start, base.schedule, weights, replace(config, anneal=anneal))
        note = f"from {base.method}, {result.iterations} iterations"
        if result.stopped_by != "iterations":
            note += f", stopped: {result.stopped_by}"
        runs.append(
            Run(
                "anneal" if anneal else "hill",
                result.schedule,
                result.objective,
                result.seconds,
                note,
            )
        )

    best = min(runs, key=total)
    exact = solve_exact(
        fleet,
        jobs,
        start,
        weights,
        upper_bound=best.schedule,
        max_plates=exact_limit,
        node_limit=node_limit,
        initial=initial,
        pinned=pinned,
    )
    if exact.schedule is None:
        runs.append(Run("exact", None, None, exact.seconds, f"skipped: {exact.note}"))
    else:
        note = f"optimal in the sequence model, {exact.nodes} nodes"
        if not exact.optimal:
            note = exact.note
        runs.append(Run("exact", exact.schedule, exact.objective, exact.seconds, note))
    return runs


def _reference(runs: Sequence[Run]) -> float | None:
    """The lowest total, used for the gap column."""
    totals = [r.objective.total for r in runs if r.objective is not None]
    return min(totals) if totals else None


def _gap(run: Run, reference: float | None) -> str:
    if run.objective is None or reference is None:
        return "-"
    if reference <= 0:
        return "0.0%" if run.objective.total <= 0 else "-"
    return f"{(run.objective.total - reference) / reference * 100:.1f}%"


def _seconds(value: float) -> str:
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def _row(run: Run, reference: float | None) -> list[str]:
    o = run.objective
    if o is None:
        return [run.method, "-", "-", "-", "-", "-", "-", "-", _seconds(run.seconds), run.note]
    return [
        run.method,
        f"{o.total:.1f}",
        _gap(run, reference),
        f"{o.tardiness:.0f}",
        format_duration(o.makespan),
        format_duration(o.wait_off),
        format_duration(o.wait_in),
        str(o.material_changes),
        _seconds(run.seconds),
        run.note,
    ]


_HEADER = [
    "method",
    "objective",
    "gap",
    "tardiness",
    "makespan",
    "wait off",
    "wait in",
    "changes",
    "time",
    "note",
]


def format_compare(runs: Sequence[Run], weights: Weights) -> str:
    reference = _reference(runs)
    plates = next((len(r.schedule.assignments) for r in runs if r.schedule is not None), 0)
    w = weights
    out = [
        f"Planner comparison ({plates} plates)",
        "",
        f"  objective = {w.tardiness:g} x tardiness + {w.makespan:g} x makespan"
        f" + {w.wait_off:g} x wait off + {w.wait_in:g} x wait in"
        f" + {w.material_changes:g} x changes",
        "  times in minutes; gap is relative to the lowest objective in this table",
        "",
    ]
    out += text_table(_HEADER, [_row(r, reference) for r in runs], {1, 2, 3, 4, 5, 6, 7, 8})
    return "\n".join(line.rstrip() for line in out) + "\n"


def compare_json(runs: Sequence[Run], weights: Weights, start: datetime) -> dict[str, Any]:
    reference = _reference(runs)
    return {
        "generator": f"printfarm-planner {__version__}",
        "start": start.isoformat(timespec="minutes"),
        "weights": weights.to_dict(),
        "methods": [
            {
                "method": r.method,
                "objective": r.objective.to_dict() if r.objective is not None else None,
                "gap": (
                    round((r.objective.total - reference) / reference, 6)
                    if r.objective is not None and reference
                    else None
                ),
                "seconds": round(r.seconds, 4),
                "note": r.note,
            }
            for r in runs
        ],
    }


def render_compare_html(
    runs: Sequence[Run], weights: Weights, title: str = "Planner comparison"
) -> str:
    """Self-contained HTML: comparison table, then the best schedule's objective and timeline."""
    reference = _reference(runs)
    rows = []
    worst = max((r.objective.total for r in runs if r.objective is not None), default=0.0)
    for run in runs:
        cells = _row(run, reference)
        share = (
            run.objective.total / worst * 100 if run.objective is not None and worst > 0 else 0.0
        )
        bar = f'<div class="bar"><i style="width:{share:.1f}%"></i></div>'
        rows.append(
            f"<tr><td>{escape(cells[0])}</td><td class=num>{escape(cells[1])}{bar}</td>"
            + "".join(f"<td class=num>{escape(c)}</td>" for c in cells[2:9])
            + f"<td>{escape(cells[9])}</td></tr>"
        )
    w = weights
    body = [
        f"<h1>{escape(title)}</h1>",
        '<p class="sub">objective = '
        f"{w.tardiness:g} &times; weighted tardiness + {w.makespan:g} &times; makespan + "
        f"{w.wait_off:g} &times; wait outside labour hours + {w.wait_in:g} &times; wait inside "
        f"+ {w.material_changes:g} &times; material changes. Times in minutes, lower is better; "
        "gap is relative to the lowest objective in the table. Run times depend on the "
        "machine.</p>",
        '<div class="table"><table><tr>'
        + "".join(
            f"<th{' class=num' if 0 < i < 9 else ''}>{escape(h)}</th>"
            for i, h in enumerate(_HEADER)
        )
        + "</tr>"
        + "".join(rows)
        + "</table></div>",
    ]
    scheduled = [r for r in runs if r.schedule is not None and r.objective is not None]
    if scheduled:
        best = min(scheduled, key=lambda r: r.objective.total if r.objective else 0.0)
        assert best.schedule is not None and best.objective is not None
        metrics = compute_metrics(best.schedule)
        body += [
            f"<h2>Best schedule: {escape(best.method)}</h2>",
            objective_table(best.objective),
            f'<div class="chart" style="margin-top:12px">{chart(best.schedule, metrics)}</div>',
            legend(best.schedule),
        ]
    body.append(
        f"<footer>Generated by printfarm-planner {escape(__version__)}. Plans are computed "
        "from slicer or model print times; they are not measurements.</footer>"
    )
    css = CSS + (
        ".bar { height:4px; background:var(--line); border-radius:2px; margin-top:3px; }"
        ".bar i { display:block; height:4px; background:var(--muted); border-radius:2px; }"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )
