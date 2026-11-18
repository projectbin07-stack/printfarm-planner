"""Text, CSV and JSON renderings of a schedule."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from . import __version__
from .metrics import Metrics
from .objective import TERMS, Objective
from .scheduler import Schedule

__all__ = [
    "TERM_LABELS",
    "format_duration",
    "format_objective",
    "format_text",
    "objective_rows",
    "text_table",
    "to_csv",
    "to_json_dict",
]

TERM_LABELS = {
    "tardiness": "weighted tardiness",
    "makespan": "makespan",
    "wait_off": "wait outside labour hours",
    "wait_in": "wait inside labour hours",
    "material_changes": "material changes",
}


def format_duration(minutes: float) -> str:
    """``1505`` -> ``"25h 05m"``; ``45`` -> ``"45m"``."""
    total = round(minutes)
    hours, mins = divmod(total, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def _number(value: float) -> str:
    return f"{value:.10g}" if abs(value) < 1e6 else f"{value:.6g}"


def objective_rows(objective: Objective) -> list[list[str]]:
    """``[label, value, weight, contribution]`` per term, then the total."""
    contributions = objective.contributions()
    rows = []
    for name in TERMS:
        raw = float(getattr(objective, name))
        value = f"{raw:g}" if name == "material_changes" else f"{raw:.0f} min"
        rows.append(
            [
                TERM_LABELS[name],
                value,
                _number(getattr(objective.weights, name)),
                f"{contributions[name]:.1f}",
            ]
        )
    rows.append(["total", "", "", f"{objective.total:.1f}"])
    return rows


def format_objective(objective: Objective) -> list[str]:
    """Text lines for the objective block (see docs/algorithm.md for the formula)."""
    lines = ["Objective (lower is better; minutes, weighted)"]
    lines += text_table(
        ["term", "value", "weight", "contribution"], objective_rows(objective), {1, 2, 3}
    )
    return lines


def _when(schedule: Schedule, minutes: float) -> str:
    return schedule.calendar.to_datetime(minutes).strftime("%a %d %b %H:%M")


def _iso(schedule: Schedule, minutes: float) -> str:
    moment: datetime = schedule.calendar.to_datetime(minutes)
    return moment.replace(microsecond=0).isoformat(timespec="minutes")


def text_table(header: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]

    def line(cells: list[str]) -> str:
        parts = [
            c.rjust(widths[i]) if i in right else c.ljust(widths[i]) for i, c in enumerate(cells)
        ]
        return "  " + "  ".join(parts).rstrip()

    return [line(header), *(line(r) for r in rows)]


def format_text(schedule: Schedule, metrics: Metrics, objective: Objective | None = None) -> str:
    out = [f"Production plan (strategy: {schedule.strategy})", ""]
    if schedule.assignments:
        out.append(
            f"  Start {_when(schedule, 0)}   Finish {_when(schedule, metrics.makespan_min)}   "
            f"Makespan {format_duration(metrics.makespan_min)}"
        )
        out.append(
            f"  {metrics.plates} plates, {format_duration(metrics.print_min)} printing, "
            f"fleet utilisation {metrics.fleet_utilisation * 100:.1f}%"
        )
        out.append(
            f"  Operator: {metrics.operator_tasks} tasks, {format_duration(metrics.operator_min)}, "
            f"{metrics.material_changes} material changes"
        )
        out.append(
            "  Finished plates waiting: "
            f"{format_duration(metrics.wait_off_hours_min)} outside labour windows, "
            f"{format_duration(metrics.wait_in_hours_min)} inside"
        )
        out.append(
            "  Of that, final plates waiting to be cleared: "
            f"{format_duration(metrics.final_wait_min)}"
        )
        if metrics.mass_g is not None:
            out.append(f"  Filament: {metrics.mass_g:.1f} g")
        out.append("")
        if objective is not None:
            out += format_objective(objective)
            out.append("")

        rows = [
            [
                s.name,
                s.model,
                str(s.plates),
                format_duration(s.print_min),
                format_duration(s.setup_min),
                format_duration(s.wait_off_hours_min),
                format_duration(s.wait_in_hours_min),
                format_duration(s.final_wait_min),
                _when(schedule, s.cleared_min) if s.plates else "-",
                f"{s.utilisation * 100:.1f}%",
            ]
            for s in metrics.printers
        ]
        out.append("Printers (wait off / wait in include the final plate's wait)")
        out += text_table(
            [
                "printer",
                "model",
                "plates",
                "printing",
                "setup",
                "wait off",
                "wait in",
                "final wait",
                "cleared",
                "util",
            ],
            rows,
            {2, 3, 4, 5, 6, 7, 9},
        )
        out.append("")

        rows = []
        for printer in schedule.printers:
            for a in schedule.for_printer(printer.name):
                rows.append(
                    [
                        a.printer,
                        a.plate.label,
                        _when(schedule, a.start),
                        _when(schedule, a.end),
                        format_duration(a.print_min),
                        "+".join(a.plate.job.spools) + (" (change)" if a.material_change else ""),
                    ]
                )
        out.append("Schedule")
        out += text_table(["printer", "plate", "start", "end", "print", "material"], rows, {4})
        out.append("")

        rows = []
        for j in metrics.jobs:
            if j.due_min is None:
                status = "-"
            elif j.late_min > 0:
                status = f"LATE {format_duration(j.late_min)}"
            else:
                status = "on time"
            due = _when(schedule, j.due_min) if j.due_min is not None else "-"
            rows.append([j.name, str(j.copies), _when(schedule, j.finish_min), due, status])
        out.append("Jobs")
        out += text_table(["job", "copies", "finished", "due", "status"], rows, {1})
        out.append("")
    else:
        out += ["  Nothing could be scheduled.", ""]

    if schedule.unscheduled:
        out.append("Unscheduled (no printer can run these)")
        for item in schedule.unscheduled:
            out.append(f"  {item.job.name}:")
            for printer_name, reasons in item.reasons.items():
                out.append(f"    {printer_name}: {'; '.join(reasons)}")
        out.append("")
    notes = sorted({f"{j.name}: {n}" for j in schedule.jobs for n in j.notes})
    if notes:
        out.append("Notes")
        out += [f"  {n}" for n in notes]
    return "\n".join(out).rstrip() + "\n"


def to_csv(schedule: Schedule) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "printer",
            "job",
            "copy",
            "operator",
            "setup_start",
            "print_start",
            "print_end",
            "print_min",
            "material_change",
            "spools",
        ]
    )
    for a in sorted(schedule.assignments, key=lambda a: (a.start, a.printer)):
        writer.writerow(
            [
                a.printer,
                a.plate.job.name,
                a.plate.copy,
                a.operator + 1,
                _iso(schedule, a.setup_start),
                _iso(schedule, a.start),
                _iso(schedule, a.end),
                f"{a.print_min:.1f}",
                "yes" if a.material_change else "no",
                "+".join(a.plate.job.spools),
            ]
        )
    return buffer.getvalue()


def to_json_dict(
    schedule: Schedule, metrics: Metrics, objective: Objective | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "generator": f"printfarm-planner {__version__}",
        "start": _iso(schedule, 0),
        "strategy": schedule.strategy,
        "assignments": [
            {
                "printer": a.printer,
                "job": a.plate.job.name,
                "copy": a.plate.copy,
                "operator": a.operator + 1,
                "setup_start": _iso(schedule, a.setup_start),
                "print_start": _iso(schedule, a.start),
                "print_end": _iso(schedule, a.end),
                "setup_start_min": round(a.setup_start, 3),
                "print_start_min": round(a.start, 3),
                "print_end_min": round(a.end, 3),
                "material_change": a.material_change,
                "spools": list(a.plate.job.spools),
            }
            for a in sorted(schedule.assignments, key=lambda a: (a.start, a.printer))
        ],
        "clearings": [
            {
                "printer": c.printer,
                "operator": c.operator + 1,
                "final_plate_end": _iso(schedule, c.ready),
                "start": _iso(schedule, c.start),
                "end": _iso(schedule, c.end),
                "final_plate_end_min": round(c.ready, 3),
                "start_min": round(c.start, 3),
                "end_min": round(c.end, 3),
            }
            for c in sorted(schedule.clearings, key=lambda c: (c.start, c.printer))
        ],
        "unscheduled": [{"job": u.job.name, "reasons": u.reasons} for u in schedule.unscheduled],
        "metrics": metrics.to_dict(),
    }
    if objective is not None:
        data["objective"] = objective.to_dict()
    return data
