"""Command-line interface: ``printfarm-planner plan|compare|check|models``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .compare import compare, compare_json, format_compare, render_compare_html
from .compat import incompatibilities
from .exact import EXACT_PLATE_LIMIT
from .fleet import Fleet, FleetError, load_fleet
from .gantt import render_html
from .jobs import JobError, load_insight_file, load_jobs_csv
from .labour import LabourError
from .metrics import compute_metrics
from .model import BUILTIN_MODELS, Job, normalise_spool
from .objective import TERMS, Weights, evaluate
from .report import format_text, to_csv, to_json_dict
from .scheduler import STRATEGIES, plan
from .search import SearchConfig, improve

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


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _add_objective_options(parser: argparse.ArgumentParser) -> None:
    defaults = Weights()
    parser.add_argument(
        "--weight",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="objective weight, repeatable; NAME is one of "
        f"{', '.join(TERMS)} (defaults: "
        + ", ".join(f"{name}={getattr(defaults, name):g}" for name in TERMS)
        + ")",
    )


def _add_search_options(parser: argparse.ArgumentParser) -> None:
    defaults = SearchConfig()
    parser.add_argument(
        "--iterations",
        type=_non_negative_int,
        default=defaults.iterations,
        help=f"local-search iteration budget (default {defaults.iterations})",
    )
    parser.add_argument(
        "--time-limit",
        type=_positive_float,
        metavar="SECONDS",
        help="local-search wall-clock limit; results then depend on machine speed",
    )
    parser.add_argument(
        "--seed", type=int, default=defaults.seed, help="local-search random seed (default 0)"
    )


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
    plan_cmd.add_argument(
        "--improve",
        choices=("hill", "anneal"),
        help="run a local-search improvement phase on the greedy schedule: hill climbing "
        "or simulated annealing",
    )
    _add_search_options(plan_cmd)
    _add_objective_options(plan_cmd)
    plan_cmd.add_argument("--html", type=Path, help="write a Gantt HTML report here")
    plan_cmd.add_argument("--csv", type=Path, help="write the schedule as CSV here")
    plan_cmd.add_argument("--json", action="store_true", help="print JSON instead of text")
    plan_cmd.add_argument("--title", default="Print farm plan", help="HTML report title")

    compare_cmd = sub.add_parser(
        "compare", help="run fill, edd, local search and the exact solver on the same input"
    )
    _add_inputs(compare_cmd)
    _add_search_options(compare_cmd)
    _add_objective_options(compare_cmd)
    compare_cmd.add_argument(
        "--exact-limit",
        type=_non_negative_int,
        default=EXACT_PLATE_LIMIT,
        metavar="PLATES",
        help="run the exact solver only up to this many plates "
        f"(default {EXACT_PLATE_LIMIT}; run time grows steeply)",
    )
    compare_cmd.add_argument("--html", type=Path, help="write an HTML comparison report here")
    compare_cmd.add_argument("--json", action="store_true", help="print JSON instead of text")
    compare_cmd.add_argument("--title", default="Planner comparison", help="HTML report title")

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
    for j in jobs:
        for note in j.notes:
            if note.startswith("warning: "):
                print(f"printfarm-planner: warning: {j.name}: {note[9:]}", file=sys.stderr)
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
    width = max(len(m.name) for m in BUILTIN_MODELS.values())
    for model in BUILTIN_MODELS.values():
        nozzles = "/".join(f"{n:g}" for n in sorted(model.nozzles_mm))
        print(
            f"{model.name:{width}}  bed {model.bed_x_mm:g} x {model.bed_y_mm:g} x "
            f"{model.bed_z_mm:g} mm  {'enclosed  ' if model.enclosed else 'open frame'}"
            f"  nozzles {nozzles} mm  up to {model.max_spools} spools"
            f"  materials {', '.join(sorted(model.materials))}"
        )
    print(
        "Bed, enclosure, nozzles and spools: manufacturer figures from the shared printer "
        "registry (sources in print_registry.json). Materials: this planner's default."
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

    weights = Weights.parse(args.weight)
    if args.command == "compare":
        return _compare(args, fleet, jobs, start, weights)

    schedule = plan(fleet, jobs, start, strategy=args.strategy)
    if args.improve is not None:
        result = improve(fleet, jobs, start, schedule, weights, _search_config(args))
        schedule = result.schedule
    metrics = compute_metrics(schedule)
    objective = evaluate(schedule, weights, metrics)
    if args.html is not None:
        html = render_html(schedule, metrics, args.title, objective)
        args.html.write_text(html, encoding="utf-8")
    if args.csv is not None:
        args.csv.write_text(to_csv(schedule), encoding="utf-8", newline="")
    if args.json:
        print(json.dumps(to_json_dict(schedule, metrics, objective), indent=2))
    else:
        sys.stdout.write(format_text(schedule, metrics, objective))
    return 1 if schedule.unscheduled else 0


def _search_config(args: argparse.Namespace) -> SearchConfig:
    return SearchConfig(
        iterations=args.iterations,
        time_limit_s=args.time_limit,
        seed=args.seed,
        anneal=getattr(args, "improve", None) == "anneal",
    )


def _compare(
    args: argparse.Namespace, fleet: Fleet, jobs: list[Job], start: datetime, weights: Weights
) -> int:
    runs = compare(fleet, jobs, start, weights, _search_config(args), exact_limit=args.exact_limit)
    if args.html is not None:
        html = render_compare_html(runs, weights, args.title)
        args.html.write_text(html, encoding="utf-8")
    if args.json:
        print(json.dumps(compare_json(runs, weights, start), indent=2))
    else:
        sys.stdout.write(format_compare(runs, weights))
    unscheduled = next((r.schedule.unscheduled for r in runs if r.schedule is not None), [])
    if unscheduled:
        names = ", ".join(u.job.name for u in unscheduled)
        print(f"Unscheduled (no printer can run these): {names}", file=sys.stderr)
    return 1 if unscheduled else 0


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
