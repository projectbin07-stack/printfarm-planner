"""Self-contained HTML report with an SVG Gantt chart (no scripts, no external assets)."""

from __future__ import annotations

from datetime import timedelta
from html import escape
from itertools import pairwise

from . import __version__
from .metrics import Metrics
from .model import material_type
from .objective import Objective
from .report import format_duration, objective_rows
from .scheduler import Schedule

__all__ = ["CSS", "chart", "legend", "objective_table", "render_html"]

_MATERIAL_COLOURS = {
    "PLA": "#3b6fd4",
    "PETG": "#14897a",
    "ABS": "#c8641e",
    "ASA": "#b8432f",
    "TPU": "#8a4fc0",
    "PC": "#5c6f82",
    "PA": "#8c6d1f",
    "PVA": "#4f8f3a",
}
_OTHER = "#6b6b6b"

_LABEL_W = 150
_ROW_H = 36
_BAR_H = 22
_AXIS_H = 42

CSS = """
:root { --bg:#ffffff; --fg:#1d1f23; --muted:#5d636d; --line:#d9dce1; --panel:#f5f6f8;
        --window:#e3f1e5; --late:#b3261e; --ok:#1e7a3c; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e7e9ec; --muted:#a0a6b0; --line:#343841; --panel:#1f2228;
          --window:#1f3326; --late:#ff8a80; --ok:#7fd49a; }
}
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--fg);
       font:14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width:1200px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:16px; margin:28px 0 8px; }
.sub { color:var(--muted); margin:0 0 16px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:10px 12px; }
.card b { display:block; font-size:20px; font-variant-numeric:tabular-nums; }
.card span { color:var(--muted); font-size:12px; }
.chart { overflow-x:auto; border:1px solid var(--line); border-radius:8px;
         background:var(--bg); }
svg text { fill:var(--fg); font-size:11px; }
svg .muted { fill:var(--muted); }
svg .grid { stroke:var(--line); }
svg .window { fill:var(--window); }
svg .bar-label { fill:#ffffff; font-size:11px; }
.legend { display:flex; flex-wrap:wrap; gap:14px; margin:8px 0 0; color:var(--muted);
          font-size:12px; }
.legend i { display:inline-block; width:12px; height:12px; border-radius:2px; margin-right:5px;
            vertical-align:-2px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
.table { overflow-x:auto; }
th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
         white-space:nowrap; }
th { color:var(--muted); font-weight:600; font-size:12px; }
td.num, th.num { text-align:right; }
.late { color:var(--late); font-weight:600; }
.ok { color:var(--ok); }
footer { color:var(--muted); font-size:12px; margin-top:32px; }
"""


def _colour(spools: tuple[str, ...]) -> str:
    return _MATERIAL_COLOURS.get(material_type(spools[0]), _OTHER) if spools else _OTHER


def chart(schedule: Schedule, metrics: Metrics) -> str:
    cal = schedule.calendar
    end = max(metrics.makespan_min, 60.0)
    hours = end / 60.0
    # Fit the report's ~1000 px content width; very long plans scroll horizontally.
    px_per_hour = max(6.0, min(40.0, 1000.0 / hours))
    width = _LABEL_W + hours * px_per_hour + 20
    height = _AXIS_H + _ROW_H * len(schedule.printers) + 8

    def x(minutes: float) -> float:
        return _LABEL_W + minutes / 60.0 * px_per_hour

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height}" '
        f'viewBox="0 0 {width:.0f} {height}" role="img" aria-label="Gantt chart of the schedule">',
        '<defs><pattern id="wait" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="none"/>'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#c0392b" stroke-width="2"/>'
        "</pattern></defs>",
    ]
    top = _AXIS_H
    bottom = height - 8
    for lo, hi in cal.windows_between(0.0, end):
        parts.append(
            f'<rect class="window" x="{x(lo):.1f}" y="{top}" width="{x(hi) - x(lo):.1f}" '
            f'height="{bottom - top}"><title>labour window</title></rect>'
        )

    start = cal.start
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    step = 6 if px_per_hour >= 8 else 12
    if start != midnight:
        parts.append(f'<text x="{x(0) + 4:.1f}" y="16">{start.strftime("%a %d %b")}</text>')
    tick = midnight
    while tick <= start + timedelta(minutes=end):
        minutes = (tick - start).total_seconds() / 60.0
        if minutes >= 0:
            if tick.hour == 0:
                parts.append(
                    f'<line class="grid" x1="{x(minutes):.1f}" y1="4" x2="{x(minutes):.1f}" '
                    f'y2="{bottom}" stroke-width="1"/>'
                )
                parts.append(
                    f'<text x="{x(minutes) + 4:.1f}" y="16">{tick.strftime("%a %d %b")}</text>'
                )
            else:
                parts.append(
                    f'<line class="grid" x1="{x(minutes):.1f}" y1="26" x2="{x(minutes):.1f}" '
                    f'y2="{bottom}" stroke-width="0.5" stroke-dasharray="2 3"/>'
                )
            parts.append(
                f'<text class="muted" x="{x(minutes) + 3:.1f}" y="34">{tick.strftime("%H")}</text>'
            )
        tick += timedelta(hours=step)

    for row, printer in enumerate(schedule.printers):
        y = top + row * _ROW_H
        bar_y = y + (_ROW_H - _BAR_H) / 2
        parts.append(
            f'<line class="grid" x1="0" y1="{y + _ROW_H}" x2="{width:.0f}" y2="{y + _ROW_H}"/>'
        )
        parts.append(f'<text x="8" y="{y + 16}">{escape(printer.name)}</text>')
        parts.append(
            f'<text class="muted" x="8" y="{y + 29}">{escape(printer.model.name)} · '
            f"{printer.nozzle_mm:g} mm</text>"
        )
        rows = schedule.for_printer(printer.name)
        for previous, current in pairwise(rows):
            if current.setup_start - previous.end > 0.5:
                parts.append(
                    f'<rect x="{x(previous.end):.1f}" y="{bar_y + _BAR_H / 2 - 3:.1f}" '
                    f'width="{x(current.setup_start) - x(previous.end):.1f}" height="6" '
                    f'fill="url(#wait)"><title>{escape(previous.plate.label)} waits '
                    f"{format_duration(current.setup_start - previous.end)} for an operator"
                    "</title></rect>"
                )
        for a in rows:
            setup_w = max(1.5, x(a.start) - x(a.setup_start))
            parts.append(
                f'<rect x="{x(a.setup_start):.1f}" y="{bar_y:.1f}" width="{setup_w:.1f}" '
                f'height="{_BAR_H}" fill="#2b2d31"><title>setup '
                f"{format_duration(a.setup_min)}"
                f"{' incl. material change' if a.material_change else ''}</title></rect>"
            )
            bar_w = x(a.end) - x(a.start)
            spools = "+".join(a.plate.job.spools)
            tip = (
                f"{a.plate.label} on {a.printer}\n"
                f"{cal.to_datetime(a.start):%a %d %b %H:%M} - "
                f"{cal.to_datetime(a.end):%a %d %b %H:%M}"
                f" ({format_duration(a.print_min)})\n{spools}"
            )
            parts.append(
                f'<rect x="{x(a.start):.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" '
                f'height="{_BAR_H}" rx="3" fill="{_colour(a.plate.job.spools)}">'
                f"<title>{escape(tip)}</title></rect>"
            )
            label = a.plate.label
            if bar_w > 7 * len(label) + 8:
                parts.append(
                    f'<text class="bar-label" x="{x(a.start) + 5:.1f}" y="{bar_y + 15:.1f}">'
                    f"{escape(label)}</text>"
                )
    parts.append("</svg>")
    return "".join(parts)


def legend(schedule: Schedule) -> str:
    used = sorted({material_type(a.plate.job.spools[0]) for a in schedule.assignments})
    items = [
        f'<span><i style="background:{_MATERIAL_COLOURS.get(m, _OTHER)}"></i>{escape(m)}</span>'
        for m in used
    ]
    items.append('<span><i style="background:#2b2d31"></i>operator setup</span>')
    items.append(
        '<span><i style="background:repeating-linear-gradient(45deg,#c0392b 0 2px,'
        'transparent 2px 5px)"></i>finished, waiting for operator</span>'
    )
    items.append('<span><i class="win" style="background:var(--window)"></i>labour window</span>')
    return '<div class="legend">' + "".join(items) + "</div>"


def objective_table(objective: Objective) -> str:
    """HTML table of the objective terms, weights and contributions."""
    parts = [
        '<div class="table"><table><tr><th>Term</th><th class="num">Value</th>'
        '<th class="num">Weight</th><th class="num">Contribution</th></tr>'
    ]
    for label, value, weight, contribution in objective_rows(objective):
        cells = f"<td class=num>{escape(value)}</td><td class=num>{escape(weight)}</td>"
        if label == "total":
            total = f"<th class=num>{escape(contribution)}</th>"
            parts.append(f"<tr><th>total</th><td></td><td></td>{total}</tr>")
        else:
            parts.append(
                f"<tr><td>{escape(label)}</td>{cells}<td class=num>{escape(contribution)}</td></tr>"
            )
    parts.append("</table></div>")
    return "".join(parts)


def render_html(
    schedule: Schedule,
    metrics: Metrics,
    title: str = "Print farm plan",
    objective: Objective | None = None,
) -> str:
    cal = schedule.calendar

    def when(minutes: float) -> str:
        return cal.to_datetime(minutes).strftime("%a %d %b %H:%M")

    cards = [
        (format_duration(metrics.makespan_min), "makespan"),
        (f"{metrics.fleet_utilisation * 100:.1f}%", "fleet utilisation (printing / makespan)"),
        (str(metrics.plates), "plates"),
        (format_duration(metrics.operator_min), f"operator time, {metrics.operator_tasks} tasks"),
        (str(metrics.material_changes), "material changes"),
        (format_duration(metrics.wait_off_hours_min), "plates waiting outside labour hours"),
        (str(metrics.late_jobs), "late jobs"),
    ]
    if metrics.mass_g is not None:
        cards.append((f"{metrics.mass_g:.0f} g", "filament"))

    body: list[str] = [
        f"<h1>{escape(title)}</h1>",
        f'<p class="sub">Start {when(0)} · finish {when(metrics.makespan_min)} · strategy '
        f"<code>{escape(schedule.strategy)}</code> · {len(schedule.printers)} printers · "
        f"{schedule.labour.operators} operator(s)</p>",
        '<div class="cards">'
        + "".join(
            f'<div class="card"><b>{escape(v)}</b><span>{escape(k)}</span></div>' for v, k in cards
        )
        + "</div>",
        "<h2>Timeline</h2>",
        f'<div class="chart">{chart(schedule, metrics)}</div>',
        legend(schedule),
    ]
    if objective is not None:
        body += [
            "<h2>Objective</h2>",
            '<p class="sub">Weighted sum, lower is better. Times in minutes; the formula is in '
            "docs/algorithm.md.</p>",
            objective_table(objective),
        ]
    body += [
        "<h2>Printers</h2>",
        '<div class="table"><table><tr><th>Printer</th><th>Model</th><th class="num">Plates</th>'
        '<th class="num">Printing</th><th class="num">Setup</th>'
        '<th class="num">Wait (off hours)</th>'
        '<th class="num">Wait (in hours)</th><th class="num">Changes</th>'
        '<th class="num">Utilisation</th></tr>',
    ]
    for s in metrics.printers:
        body.append(
            f"<tr><td>{escape(s.name)}</td><td>{escape(s.model)}</td><td class=num>{s.plates}</td>"
            f"<td class=num>{format_duration(s.print_min)}</td>"
            f"<td class=num>{format_duration(s.setup_min)}</td>"
            f"<td class=num>{format_duration(s.wait_off_hours_min)}</td>"
            f"<td class=num>{format_duration(s.wait_in_hours_min)}</td>"
            f"<td class=num>{s.material_changes}</td>"
            f"<td class=num>{s.utilisation * 100:.1f}%</td></tr>"
        )
    body.append("</table></div>")

    body.append(
        '<h2>Jobs</h2><div class="table"><table><tr><th>Job</th><th class="num">Copies</th>'
        "<th>Printers</th><th>First start</th><th>Finished</th><th>Due</th><th>Status</th></tr>"
    )
    for j in metrics.jobs:
        if j.due_min is None:
            status = "<td>-</td>"
        elif j.late_min > 0:
            status = f'<td class="late">late by {format_duration(j.late_min)}</td>'
        else:
            status = '<td class="ok">on time</td>'
        due = when(j.due_min) if j.due_min is not None else "-"
        body.append(
            f"<tr><td>{escape(j.name)}</td><td class=num>{j.copies}</td>"
            f"<td>{escape(', '.join(j.printers))}</td><td>{when(j.first_start_min)}</td>"
            f"<td>{when(j.finish_min)}</td><td>{due}</td>{status}</tr>"
        )
    body.append("</table></div>")

    if schedule.unscheduled:
        body.append("<h2>Unscheduled</h2><ul>")
        for u in schedule.unscheduled:
            reasons = "; ".join(
                f"{escape(p)}: {escape(', '.join(r))}" for p, r in u.reasons.items()
            )
            body.append(f"<li><b>{escape(u.job.name)}</b> - {reasons}</li>")
        body.append("</ul>")

    body.append(
        f"<footer>Generated by printfarm-planner {escape(__version__)}. Times are plan "
        "estimates from slicer or model print times; they are not measurements.</footer>"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )
