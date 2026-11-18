# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-19

### Added

- Explicit, configurable objective: priority-weighted tardiness, makespan, waiting outside and
  inside labour windows, and material changes (`--weight NAME=VALUE`). `plan` reports each term,
  its weight and its contribution in text, JSON (`"objective"`) and the HTML report. Formula and
  default weights are in `docs/algorithm.md`.
- Local-search improvement phase (`plan --improve hill|anneal`, `--iterations`, `--time-limit`,
  `--seed`): move, swap and reassign-printer neighbourhoods, hill climbing or simulated annealing
  with geometric cooling. Candidates are re-timed through the same dispatcher as the greedy
  planner. The best plan is returned, so the result is never worse than the start.
- Branch-and-bound exact solver over plate-to-printer assignment and order for small instances
  (default limit 8 plates, node limit 2,000,000).
- `compare` command: `fill`, `edd`, hill climbing, annealing and the exact solver on one input,
  with objective terms, gap and run time per method, as text, JSON (`--json`) or an HTML report
  (`--html`).
- `examples/small-jobs.csv`: an invented 8-plate order small enough for the exact solver.
- `docs/algorithm.md`, and `tests/gap_study.py`, which measures the heuristics' optimality gap
  on generated instances. The measured gaps are pinned in the tests and listed in the docs.

### Changed

- Plate booking moved into a `Dispatcher` class shared by the greedy planner, the new sequence
  decoder `simulate`, the local search and the exact solver. Greedy plans are unchanged: the
  example plan and all v0.1.0 scheduler tests give the same results.
- Text and HTML plan reports include an "Objective" section.

### Known gaps

- The local search can stop in a local optimum. On one generated 7-plate instance it stayed 147%
  above the optimum. On the 26-plate example there is no exact reference.
- The wait after each printer's last plate is not in the objective, so plans that end overnight
  are not penalised for it.
- Default weights are a judgement call, not calibrated against a real farm.

## [0.1.0] - 2026-11-18

First release.

### Added

- Fleet file (JSON): built-in Bambu Lab P1S, A1 and A1 Mini models plus custom models; per-printer
  nozzle, AMS slots, allowed materials, loaded spools, time factor and `count`.
- Labour calendar: weekday windows, split shifts, windows that run past midnight, and `24/7`.
  Plate changes and material changes are operator tasks that must fit inside a window, and each
  operator does one task at a time.
- Job input from a CSV plan, from `gcode-insight analyze --json` output, or both. A CSV row
  can pull time, material, nozzle, size and mass from a gcode-insight JSON and override any of
  them.
- Compatibility checks: material, filament count against AMS slots, nozzle, bed fit with 90
  degree rotation, and printer models the file was sliced for.
- Greedy scheduler with `fill` (minimise finished plates waiting for an operator) and `edd`
  (earliest due date) strategies, priorities, and material-change awareness.
- Metrics: makespan, per-printer and fleet utilisation, operator time, material changes, waiting
  split into outside and inside labour hours, and job lateness.
- Text, CSV, JSON and a self-contained HTML report with an SVG Gantt chart that has a dark mode.
- CLI commands `plan`, `check` and `models`.

### Known gaps

- Not yet used on a real print farm. Example jobs are invented, and print times come from slicer
  or model estimates. See the README section "Known limitations".

[0.2.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.2.0
[0.1.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.1.0
