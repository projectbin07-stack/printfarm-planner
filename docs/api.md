# Library API

`printfarm-planner` can be used in-process, without the CLI. Everything below is exported from
the package itself (`from printfarm_planner import plan`) and listed in
`printfarm_planner.__all__`. All of it has type hints (the package ships `py.typed`).

## Stability

From 0.6.0 the names in `__all__` follow semantic versioning. Within a `0.MINOR` series, and
from 1.0 within a major version, they are not removed, renamed or changed incompatibly. New
parameters are added as keyword-only parameters whose defaults keep the earlier behaviour,
and new fields of the result types get defaults. A dependent project can therefore pin a minor
version (`printfarm-planner @ git+...@v0.6.0`, accepting later `0.6.x` fixes).

Not covered: module members that are not exported here (import them from their module at your
own risk), the CLI's text and HTML layout, and run times. The JSON written by the CLI has its
own `generator` field.

## Example: re-plan a running farm

```python
from datetime import datetime
from pathlib import Path

from printfarm_planner import (
    Plate,
    PrinterState,
    SearchConfig,
    evaluate,
    improve,
    load_fleet,
    load_jobs_csv,
    plan,
)

start = datetime(2026, 11, 23, 8, 0)
fleet = load_fleet(Path("examples/fleet.json"))
jobs = load_jobs_csv(Path("examples/small-jobs.csv"), start)
by_name = {job.name: job for job in jobs}

# P1S-1 is printing until 10:30 (150 min from now); A1-2 is in maintenance.
initial = {
    "P1S-1": PrinterState(available_at_min=150, plate_on_bed=True),
    "A1-2": PrinterState(offline=True),
}
# The second enclosure lid was already sent to A1-1: it must stay there, first.
pinned = {"A1-1": [Plate(by_name["enclosure-lid"], 2)]}

schedule = plan(fleet, jobs, start, initial=initial, pinned=pinned)
result = improve(fleet, jobs, start, schedule, config=SearchConfig(iterations=500, seed=1))
for a in result.schedule.assignments:
    print(f"{a.printer:8} {a.plate.label:18} setup {a.setup_start:6.0f}  end {a.end:6.0f}")
print("objective", round(evaluate(schedule).total, 1), "->", round(result.objective.total, 1))
```

Output (0.6.0, run from the repository root):

```text
P1S-2    asa-bracket #2     setup      0  end    195
A1-1     enclosure-lid #2   setup      5  end    580
P1S-1    mini-hinges #2     setup    150  end    210
P1S-2    asa-bracket #1     setup    195  end    390
P1S-1    mini-hinges #1     setup    210  end    270
P1S-1    logo-badge         setup    270  end    420
P1S-1    enclosure-lid #1   setup    420  end    995
A1-1     enclosure-base     setup    580  end   1380
objective 2742.5 -> 1553.5
```

Nothing is planned on A1-2 or before minute 150 on P1S-1, and the pinned lid stays first on
A1-1. `improve` reads the starting state and the pinned plates from the schedule it improves.

## Reference

Times are minutes from the plan start unless a name says otherwise.

### Inputs

| Name | What it is |
|---|---|
| `Fleet(printers, labour)`, `Labour(weekly, operators, plate_change_min, material_change_min)` | the fleet and its operator rules |
| `Printer(name, model, nozzle_mm, ams_slots, materials, time_factor, loaded)` | one printer; `loaded` is the spools in it now (`"PLA:#161616"`) |
| `PrinterModel`, `BUILTIN_MODELS`, `builtin_model(name)`, `canonical_model_name(text)` | printer models; the built-in ones come from the shared registry |
| `load_fleet(path)`, `parse_fleet(data)`, `FleetError` | the fleet JSON file |
| `Job(name, print_time_min, spools, copies, nozzle_mm, size_mm, mass_g, due_min, priority, models, source, notes)` | a print job: `copies` identical plates |
| `Plate(job, copy)` | one copy of a job (1-based), the unit that is scheduled |
| `job_from_insight(data, name, ...)`, `load_insight_file(path, ...)`, `INSIGHT_SCHEMA_VERSION` | a job from gcode-insight `analyze --json` output (or the same dict built in-process with `Analysis.to_dict()`) |
| `load_jobs_csv(path, start, ...)`, `JobError` | the jobs CSV |
| `Weekly`, `parse_weekly(spec)`, `Calendar`, `LabourError` | labour windows |
| `PrinterState(available_at_min=0, plate_on_bed=False, offline=False)` | a printer's state when the plan starts (see below) |

### Planning

| Name | What it does |
|---|---|
| `plan(fleet, jobs, start, strategy="fill", calendar=None, *, initial=None, pinned=None) -> Schedule` | greedy plan (`STRATEGIES`: `fill`, `edd`) |
| `improve(fleet, jobs, start, initial_schedule, weights=None, config=None) -> SearchResult` | local search from a schedule; never worse than its start |
| `SearchConfig(iterations, time_limit_s, seed, anneal, t0, t_end_ratio)`, `NEIGHBOURHOODS` | search budget and rule |
| `solve_exact(fleet, jobs, start, weights=None, upper_bound=None, max_plates=8, node_limit=2_000_000, *, initial=None, pinned=None) -> ExactResult` | branch and bound for up to `EXACT_PLATE_LIMIT` plates |
| `simulate(fleet, jobs, start, sequences, strategy="sequence", calendar=None, *, initial=None, pinned=None) -> Schedule` | time a given plate order per printer |
| `sequences_of(schedule)` | the plate order per printer of a schedule |
| `compare(fleet, jobs, start, weights=None, config=None, exact_limit=8, node_limit=2_000_000, *, initial=None, pinned=None) -> list[Run]` | every planner on one input |
| `Dispatcher` | the booking core all planners share (`place`, `clear`, `slot`, `copy`, `is_pinned`, `free_plates`) |
| `incompatibilities(printer, job) -> list[str]` | why a printer cannot run a job (empty if it can) |

`initial` maps printer names to `PrinterState`:

- `available_at_min`: when the printer is free for its next operator task. For a printer
  that is printing, the predicted end of the print.
- `plate_on_bed`: a plate is on the bed (or will be at `available_at_min`). The next plate
  change removes it; if the plan gives the printer no plate, a clearing task is booked. Its
  wait counts in the objective.
- `offline`: no plates and no tasks. Jobs only offline printers could run are unscheduled.

`pinned` maps printer names to plates that must be that printer's first plates, in order
(dispatched, not started). No planner moves them. Plates are matched by job name and copy
number. Unknown printers, plates that are not schedulable, a printer that cannot run its pinned
plate, and a plate pinned twice raise `ValueError`.

### Results

| Name | What it holds |
|---|---|
| `Schedule` | `assignments`, `clearings`, `unscheduled`, `initial`, `pinned`; `for_printer(name)`, `clearing_for(name)`, `state_of(name)` |
| `Assignment` | `printer`, `plate`, `operator`, `ready`, `setup_start`, `start`, `end`, `material_change`, `loaded`; `setup_min`, `print_min` |
| `Clearing` | the task that removes a printer's final plate |
| `Unscheduled` | a job no printer can run, with the reasons per printer |
| `Weights`, `Objective`, `TERMS`, `evaluate(schedule, weights=None)` | the objective (see [algorithm.md](algorithm.md)) |
| `Metrics`, `PrinterStats`, `JobResult`, `compute_metrics(schedule)` | makespan, utilisation, waiting, lateness |
| `SearchResult`, `ExactResult`, `Run` | results of `improve`, `solve_exact`, `compare` |
| `to_json_dict(schedule, metrics, objective=None)`, `render_html(schedule, metrics, title, objective=None)` | the CLI's JSON and HTML Gantt output |
| `base_material(name)`, `spool_matches(loaded, needed)` | material and spool matching rules |
