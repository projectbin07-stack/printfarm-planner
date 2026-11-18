"""Command-line interface: ``printfarm-planner plan|check|models``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .compat import incompatibilities
from .fleet import Fleet, FleetError, load_fleet
from .gantt import render_html
from .jobs import JobError, load_insight_file, load_jobs_csv
from .labour import LabourError
from .metrics import compute_metrics
from .model import BUILTIN_MODELS, Job, normalise_spool
from .report import format_text, to_csv, to_json_dict
from .scheduler import STRATEGIES, plan

__all__ = ["build_parser", "main"]


def _start(text: str | None) -> datetime:
    if text is None:
        now = datetime.now().replace(second=0, microsecond=0)
        return now.replace(minute=0) + timedelta(hours=1) if now.minute else now
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad --start {text!r}; use YYYY-MM-DDTHH:MM") from None


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fleet", type=Path, required=True, help="fleet JSON file")
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="jobs CSV file(s) and/or gcode-insight JSON analyses (analyze --json)",
    )
    parser.add_argument(
        "--start",
        help="plan start, local time YYYY-MM-DDTHH:MM (default: the next full hour)",
    )
    parser.add_argument(
        "--copies",
        type=_positive_int,
        default=1,
        help="copies of each job given directly as a JSON analysis (default 1)",
    )
    parser.add_argument(
        "--material",
        help="material for JSON analyses whose filament type is unknown, e.g. PLA",
    )
    parser.add_argument(
        "--prep-min",
        type=float,
        default=0.0,
        help="minutes added to gcode-insight model times (which exclude heating and "
        "homing) when a file has no slicer estimate (default 0)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="printfarm-planner",
        description="Plan a production run across a mixed 3D-printer fleet.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan", help="build a schedule")
    _add_inputs(plan_cmd)
    plan_cmd.add_argument(
        "--strategy",
        choices=STRATEGIES,
        default="fill",
        help="fill: minimise finished plates waiting for an operator; "
        "edd: earliest due date first (default fill)",
    )
    plan_cmd.add_argument("--html", type=Path, help="write a Gantt HTML report here")
    plan_cmd.add_argument("--csv", type=Path, help="write the schedule as CSV here")
    plan_cmd.add_argument("--json", action="store_true", help="print JSON instead of text")
    plan_cmd.add_argument("--title", default="Print farm plan", help="HTML report title")

    check_cmd = sub.add_parser("check", help="show which printers can run which jobs")
    _add_inputs(check_cmd)

    sub.add_parser("models", help="list built-in printer models")
    return parser


def _load_jobs(args: argparse.Namespace, start: datetime) -> list[Job]:
    material = normalise_spool(args.material) if args.material else None
    jobs: list[Job] = []
    for path in args.inputs:
        if path.suffix.lower() == ".csv":
            jobs.extend(load_jobs_csv(path, start, prep_min=args.prep_min))
        else:
            jobs.append(
                load_insight_file(
                    path,
                    copies=args.copies,
                    default_material=material,
                    prep_min=args.prep_min,
                )
            )
    names = [j.name for j in jobs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise JobError(f"duplicate job name(s) across inputs: {', '.join(duplicates)}")
    if not jobs:
        raise JobError("no jobs found in the inputs")
    return jobs


def _check(fleet: Fleet, jobs: list[Job]) -> int:
    width = max(len(j.name) for j in jobs)
    status = 0
    for job in jobs:
        ok = [p.name for p in fleet.printers if not incompatibilities(p, job)]
        print(f"{job.name.ljust(width)}  {', '.join(ok) if ok else 'NO PRINTER'}")
        if not ok:
            status = 1
            for p in fleet.printers:
                print(f"{' ' * width}    {p.name}: {'; '.join(incompatibilities(p, job))}")
    return status


def _models() -> int:
    for model in BUILTIN_MODELS.values():
        print(
            f"{model.name:8}  bed {model.bed_x_mm:g} x {model.bed_y_mm:g} x {model.bed_z_mm:g} mm"
            f"  {'enclosed' if model.enclosed else 'open frame'}"
            f"  materials {', '.join(sorted(model.materials))}"
        )
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.command == "models":
        return _models()
    start = _start(args.start)
    fleet = load_fleet(args.fleet)
    jobs = _load_jobs(args, start)
    if args.command == "check":
        return _check(fleet, jobs)

    schedule = plan(fleet, jobs, start, strategy=args.strategy)
    metrics = compute_metrics(schedule)
    if args.html is not None:
        args.html.write_text(render_html(schedule, metrics, args.title), encoding="utf-8")
    if args.csv is not None:
        args.csv.write_text(to_csv(schedule), encoding="utf-8", newline="")
    if args.json:
        print(json.dumps(to_json_dict(schedule, metrics), indent=2))
    else:
        sys.stdout.write(format_text(schedule, metrics))
    return 1 if schedule.unscheduled else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    except (FleetError, JobError, LabourError, ValueError) as exc:
        print(f"printfarm-planner: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"printfarm-planner: error: {exc}", file=sys.stderr)
        return 2
