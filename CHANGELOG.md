# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.1.0
