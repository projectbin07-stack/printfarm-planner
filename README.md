# printfarm-planner

[![CI](https://github.com/projectbin07-stack/printfarm-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/projectbin07-stack/printfarm-planner/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Plans a production run across a mixed 3D-printer fleet (Bambu Lab P1S, A1 and A1 Mini, or your
own models), taking into account when someone is actually there to change plates.

![Gantt report for the example plan](docs/gantt.png)

## Why

On a small print farm, the bottleneck is often the person, not the printer. A 13-hour plate
started at 17:40 finishes overnight and waits on the bed until morning, which is fine. The same
plate started at 08:00 finishes at 21:15 and leaves the printer idle all night. Material is
another constraint: an ASA job cannot go to an open-frame A1, a four-colour badge needs an AMS,
and swapping spools costs operator time.

Spreadsheets and slicer queues do not model any of that. `printfarm-planner` takes a fleet
description, labour hours and a list of jobs, and produces a schedule, utilisation figures and a
Gantt chart you can open in any browser. It reads [gcode-insight](https://github.com/projectbin07-stack/gcode-insight)
JSON directly, so print time, filament, nozzle and size can come from the sliced file.

## Features

- **Fleet file** (JSON): built-in P1S / A1 / A1 Mini models with Bambu Lab's published build
  volumes, plus your own custom models. Each printer has a nozzle size, AMS slots, allowed
  materials, the spools loaded at the start, an optional speed `time_factor`, and a `count` for
  identical machines.
- **Labour windows**: weekday ranges (`mon-fri`), split shifts, shifts that run past midnight,
  or `24/7`. Plate changes and material changes are operator tasks that must fit inside a window,
  and each operator does one task at a time.
- **Jobs** from a CSV plan, from `gcode-insight analyze --json` files, or both. A CSV row can
  point at a JSON analysis and override any field.
- **Compatibility checks** with reasons: material, filament count against AMS slots, nozzle,
  bed fit (including a 90 degree rotation), and which printer models the file was sliced for.
- **Two strategies**: `fill` minimises the time finished plates wait for an operator, and `edd`
  runs earliest due date first. Both honour job priority and avoid unnecessary material changes.
- **Metrics**: makespan, utilisation per printer and for the fleet, operator time, material
  changes, waiting time split into outside and inside labour hours, and lateness per job.
- **Outputs**: terminal text, CSV, JSON, and a self-contained HTML Gantt report with no scripts
  or external assets. It follows the system light/dark setting.
- No runtime dependencies (standard library only), Python 3.10 or newer.

## Install

Not on PyPI yet. Install from the repository:

```console
pip install git+https://github.com/projectbin07-stack/printfarm-planner.git
```

or from a clone with `pip install -e .`

## Quick start

The repository ships an example fleet (two P1S, two A1 and one A1 Mini, one operator Mon-Fri
08:00-18:00 and Saturday morning) and an example order. **The jobs are invented demonstration
data, not a real order book, and their print times are not measured.**

```console
printfarm-planner plan --fleet examples/fleet.json examples/jobs.csv --start 2026-11-23T08:00 --html plan.html --csv plan.csv
```

Output (first part; the full text also lists every plate and job):

```text
Production plan (strategy: fill)

  Start Mon 23 Nov 08:00   Finish Tue 24 Nov 12:30   Makespan 28h 30m
  26 plates, 83h 53m printing, fleet utilisation 58.9%
  Operator: 26 tasks, 2h 50m, 4 material changes
  Finished plates waiting: 51h 05m outside labour windows, 1h 29m inside
  Filament: 1822.2 g

Printers
  printer  model    plates  printing  setup  wait off  wait in   util
  P1S-1    P1S           5   27h 00m    25m    1h 05m       0m  94.7%
  P1S-2    P1S           4   12h 40m    20m   14h 00m      15m  44.4%
  A1-1     A1            5   15h 16m    55m   11h 35m      10m  53.6%
  A1-2     A1            4   16h 45m    30m   10h 25m      15m  58.8%
  A1-Mini  A1 Mini       8   12h 12m    40m   14h 00m      49m  42.8%
```

The same input with `--strategy edd` gave a 29h 10m makespan, 5 material changes and 39h 40m of
off-hours waiting. On this example, `edd` left less overnight waiting than `fill`. Neither
strategy is better in general: both are greedy heuristics, so try both on your own order.

Check which printers can run which jobs, without scheduling:

```console
$ printfarm-planner check --fleet examples/fleet.json examples/jobs.csv
cable-clips       P1S-1, A1-1, A1-2, A1-Mini
enclosure-lid     P1S-1, A1-1, A1-2
enclosure-base    P1S-1, A1-1, A1-2
asa-bracket       P1S-2
logo-badge        P1S-1, A1-1, A1-2
mini-hinges       P1S-1, A1-1, A1-2, A1-Mini
calibration-cube  P1S-1, A1-1, A1-2, A1-Mini
```

When a job has no capable printer, `check` prints each printer's reasons, and `plan` lists the
job under "Unscheduled" and exits with status 1.

### Using gcode-insight output

```console
gcode-insight analyze bracket.gcode --json > bracket.json
printfarm-planner plan --fleet fleet.json bracket.json --copies 6 --start 2026-11-23T08:00
```

For each analysis, the job uses:

| Field | Taken from |
|---|---|
| print time | the slicer's total estimate in the file. If there is none, gcode-insight's model estimate plus `--prep-min` (the model excludes heating, homing and levelling). |
| materials | each tool that extruded, from gcode-insight's material, falling back to the slicer's filament type, then `--material` |
| nozzle | slicer metadata, if present |
| size | extent of all moves (X/Y span, max Z). This includes purge lines, so it can overstate the part. |
| mass | gcode-insight's mass estimate |

## Input formats

### Fleet file

```json
{
  "labour": {
    "windows": {"mon-fri": "08:00-18:00", "sat": "09:00-13:00"},
    "operators": 1,
    "plate_change_min": 5,
    "material_change_min": 10
  },
  "models": {
    "BigBox": {"bed_mm": [350, 350, 400], "enclosed": true, "materials": ["PLA", "PETG", "ABS"]}
  },
  "printers": [
    {"name": "P1S-1", "model": "P1S", "nozzle_mm": 0.4, "ams_slots": 4,
     "loaded": ["PLA:black", "PLA:white"]},
    {"name": "A1", "model": "A1", "count": 2, "ams_slots": 4},
    {"name": "Mini", "model": "A1 Mini", "time_factor": 1.1}
  ]
}
```

- `windows` takes a day spec (`mon`, `mon-fri`, `fri-mon`, `sat,sun`, `daily`) mapped to one or
  more `HH:MM-HH:MM` ranges (`"08:00-12:00, 13:00-17:00"`). A range that ends before it starts
  runs past midnight. Use `"windows": "24/7"` for an always-staffed farm. The default is Mon-Fri
  08:00-18:00.
- `ams_slots` is the number of spools available without a manual swap: 4 per AMS or AMS lite,
  and 0 for the external holder only (one spool).
- `materials` overrides the model's allowed material types. The built-in defaults are
  conservative: open-frame printers are not offered ABS, ASA, PC or PA. Run `printfarm-planner
  models` to see them.
- `loaded` lists the spools on the printer at plan start. If it is empty, the printer is assumed
  to be prepared for its first plate.
- `time_factor` multiplies every print time on that printer. It is your own estimate, for
  example for a printer you run in a slower profile. The tool does not measure it.

### Jobs CSV

```csv
name,copies,print_time,material,nozzle_mm,size_mm,mass_g,due,priority,models,gcode_json
enclosure-lid,2,9h30m,PETG:black,0.4,220x160x30,210,48,1,P1S+A1,
logo-badge,3,2:25,PLA:white+PLA:orange,0.4,80x80x6,22,2026-11-24T14:00,0,,
bracket,6,,,,,,,0,,analyses/bracket.json
```

| Column | Meaning |
|---|---|
| `name` | required, unique |
| `copies` | number of plates (default 1) |
| `print_time` | per plate: minutes (`90`), `H:MM` or `1h30m`. Required unless `gcode_json` is given. |
| `material` | spools joined with `+`. Each spool is a type, optionally with `:label` (colour). The type is checked against the printer, and the full name decides whether a spool swap is needed. |
| `nozzle_mm`, `size_mm` (`XxYxZ`), `mass_g` | optional; used for checks and totals |
| `due` | hours after the plan start, or an ISO date-time |
| `priority` | integer, higher is scheduled first (default 0) |
| `models` | printer models the file was sliced for, joined with `+` (empty means any) |
| `gcode_json` | path to a gcode-insight analysis, relative to the CSV. Values in the row override it. |

Lines starting with `#` are ignored.

## How the scheduler works

Times are minutes from `--start`, in naive local time.

1. Every plate start is an operator task lasting `plate_change_min`, plus `material_change_min`
   when the printer does not already hold all the spools the plate needs. The task must fit
   inside a single labour window, and an operator can only do one task at a time. Printing needs
   nobody, so prints run through nights and weekends.
2. The scheduler repeatedly takes the printer whose next possible operator slot comes first.
   From the plates that printer can run, it picks one by:
   - `fill`: priority, then the shortest time the finished plate would wait for the next labour
     window, then no material change, then earliest due date, then longest print, then plates
     that fewer printers can run.
   - `edd`: priority, then earliest due date, then no material change, then longest print, then
     the same flexibility tie-break.
3. Loaded spools are tracked per printer. With an AMS, the most recently used spools stay
   loaded, up to the slot count.

This is a fast, explainable greedy heuristic. It does not search for an optimal schedule.

## Architecture

```text
src/printfarm_planner/
  model.py      Printer models (built-in P1S/A1/A1 Mini), printers, jobs, plates
  fleet.py      Fleet JSON loader and validation
  labour.py     Weekly windows, lazily generated calendar, operator booking
  jobs.py       Jobs CSV and gcode-insight JSON loaders
  compat.py     Printer/job compatibility with reasons
  scheduler.py  Greedy list scheduler (fill / edd)
  metrics.py    Utilisation, waiting split, lateness
  report.py     Text, CSV and JSON output
  gantt.py      Self-contained HTML + SVG Gantt report
  cli.py        plan / check / models
```

## Command reference

```text
usage: printfarm-planner [-h] [--version] {plan,check,models} ...

    plan               build a schedule
    check              show which printers can run which jobs
    models             list built-in printer models
```

```text
usage: printfarm-planner plan [-h] --fleet FLEET [--start START]
                              [--copies COPIES] [--material MATERIAL]
                              [--prep-min PREP_MIN] [--strategy {fill,edd}]
                              [--html HTML] [--csv CSV] [--json]
                              [--title TITLE]
                              inputs [inputs ...]

  inputs                jobs CSV file(s) and/or gcode-insight JSON analyses
                        (analyze --json)
  --fleet FLEET         fleet JSON file
  --start START         plan start, local time YYYY-MM-DDTHH:MM (default: the
                        next full hour)
  --copies COPIES       copies of each job given directly as a JSON analysis
                        (default 1)
  --material MATERIAL   material for JSON analyses whose filament type is
                        unknown, e.g. PLA
  --prep-min PREP_MIN   minutes added to gcode-insight model times (which
                        exclude heating and homing) when a file has no slicer
                        estimate (default 0)
  --strategy {fill,edd}
                        fill: minimise finished plates waiting for an
                        operator; edd: earliest due date first (default fill)
  --html HTML           write a Gantt HTML report here
  --csv CSV             write the schedule as CSV here
  --json                print JSON instead of text
  --title TITLE         HTML report title
```

`check` takes the same input options as `plan`. Exit status: 0 for success, 1 when some job
cannot run on any printer, and 2 for invalid input.

## Testing

```console
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest --cov
```

The 160 tests include scheduler scenarios with timelines worked out by hand (for example, a
12-hour plate started Monday 08:05 ends at 20:05, and its successor is set up at Tuesday 08:00).
They also cover calendar edge cases (weekends, shifts past midnight, windows too short for a
task), operator contention, and loading the real JSON output of gcode-insight. CI runs on Ubuntu
and Windows with Python 3.10 and 3.13.

## Known limitations

- **Not yet used on a real farm.** The example order is invented, and no plan from this tool has
  been compared against what happened on real printers.
- Print times are slicer or model estimates. Failures, reprints, first-layer checks and
  maintenance are not modelled.
- Greedy heuristic: plans are reasonable but not optimal. The two strategies can rank
  differently on different orders.
- One operator task per plate start. The final plate on each printer is not cleared in the
  plan, and loading AMS spools is assumed to happen within `material_change_min`.
- Material compatibility is by type only. TPU in an AMS, hardened nozzles for CF filaments and
  similar rules are left to your own `materials` lists.
- Wall-clock times are naive local time, so daylight-saving changes are not modelled.
- Built-in material defaults are a conservative choice, not manufacturer limits.

## Roadmap

- Plate-batching: put several small jobs on one plate when they share material.
- Improvement pass (local search) on top of the greedy schedule.
- Downtime and maintenance windows per printer.
- Read `.gcode.3mf` plate lists through gcode-insight.

## License

MIT, see [LICENSE](LICENSE).
