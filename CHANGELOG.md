# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-11-24

This release changes planning results for files analysed by gcode-insight and for spools with
colour labels; the README example figures were re-measured.

### Fixed

- **False material changes.** A job spool without a colour (`PLA`, which is all gcode-insight
  0.1.0 could produce) never matched a loaded, labelled spool (`PLA:black`), so every such job was
  charged a material change. A spool without a label now matches any loaded spool of its type, and
  a slicer colour code matches a named spool of the nearest reference colour (`#161616` is
  `black`).
- **Colour was lost on import.** With gcode-insight 0.2.0 output each spool now carries the
  filament colour (`PLA:#FFFFFF`), so a two-colour PLA job needs two PLA spools and colour swaps
  are counted.
- **Files could run on the wrong printer model.** The model a file was sliced for
  (`printer_model`, gcode-insight 0.2.0+) was ignored; a file sliced for a P1S was offered to the
  A1s, and one sliced for a Voron to a P1S. The job is now limited to that model (`Bambu Lab P1S`
  -> `P1S`); the CSV `models` column overrides it, with a warning.
- **The README's PowerShell pipeline failed.** JSON, CSV and fleet files written by Windows
  PowerShell (UTF-8 with a BOM, or UTF-16) were rejected with `Unexpected UTF-8 BOM`. They are now
  read; the README uses gcode-insight's new `--out` instead of `>`.
- **A `due` with a UTC offset crashed** with a `TypeError` traceback and exit 1 (the code for
  "unschedulable"). It is now converted to the plan's time zone.
- **CSV values silently overrode the analysis.** A CSV value that is clearly different from the
  `gcode_json` analysis (time or mass more than 10% apart, another material or colour, nozzle or
  models) still wins, but now adds a `warning:` note and prints a warning to stderr.

### Added

- `gcode-insight` JSON `schema_version` handling: 1 (0.1.0, no field) and 2 (0.2.0) load; a newer
  version loads with a note.
- CI `interop` job that installs gcode-insight v0.2.0 and checks that its live output imports like
  the committed fixtures, including a PowerShell 5.1 redirect on Windows.
- Test fixtures from gcode-insight 0.2.0 (with the G-code they come from); the 0.1.0 output is
  kept in `tests/fixtures/gcode-insight-0.1.0/`.

## [0.3.0] - 2026-11-19

This release changes planning results. **The wait figures of v0.2.0 understated the wait of
improved plans:** v0.2.0 neither cleared each printer's final plate in the plan nor counted the
time that plate sat on the bed. The local search exploited this by ending every printer
overnight, which showed as 0 minutes of waiting although the last plates waited until the next
staffed window.

### Changed

- Every printer that ran a plate now gets a clearing task for its final plate
  (`plate_change_min`), booked by the same dispatch rule as plate starts: inside a labour window,
  with a free operator. The makespan ends when the last clearing ends.
- The final plate's wait until it is cleared now counts in `wait_off` / `wait_in`, and so in the
  objective. The formula in `docs/algorithm.md` writes the sums out. Tardiness still uses the
  print finish.
- Exact solver: closing a used printer books its clearing, and the lower bound includes the
  unavoidable final wait and clearing. It still matches brute-force enumeration on all 16 test
  instances.
- Greedy plate choice is unchanged, but clearings now take operator time. On the example order
  the `fill` plan's plate times are identical; in the `edd` plan one clearing delays seven plate
  starts by 5 minutes.

### Added

- Text and HTML reports show each printer's final-plate wait and when it is cleared; the Gantt
  chart draws the final wait and the clearing task. JSON output has a `"clearings"` list and
  `metrics.final_wait_min`.

### Results before and after (example data, default weights, seed 0, 2000 iterations)

26-plate `examples/jobs.csv`:

| method | v0.2.0 objective | v0.2.0 wait off | v0.3.0 objective | v0.3.0 wait off |
|---|---:|---:|---:|---:|
| fill | 2407.5 | 51h 05m | 2419.2 | 51h 05m |
| edd | 2313.5 | 39h 40m | 2464.5 | 50h 35m |
| hill | 1332.5 | 0m | 1867.5 | 17h 45m |
| anneal | 1342.5 | 0m | 1745.2 | 17h 50m |

v0.2.0 showed hill climbing 42.4% below the better greedy plan (`edd`); v0.3.0 shows 22.8%
(hill climbing) and 27.9% (annealing) below the better greedy plan (now `fill`). The
improvement is still there, but smaller than v0.2.0 reported. Re-timed with clearing, the v0.2.0
hill-climbing plan scores 1907.0 with 32h 52m of off-hours waiting.

8-plate `examples/small-jobs.csv`: `fill` 940.0 to 1845.5, `edd` 955.0 to 2057.5, exact optimum
810.0 to 1455.0 (hill climbing and annealing reach it in both versions). All plans now end on
Tuesday morning, when the plates that printed overnight are cleared.

Measured gaps on generated instances (`tests/gap_study.py`) changed too; for example hill
climbing's worst 7-plate gap went from 147% to 78% (same instance). The new table is in
`docs/algorithm.md`. Exact-solver run times measured for this release: up to 0.25 s for 8
plates and 7.0 s for 10 plates on generated instances.

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

[0.4.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.4.0
[0.3.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.3.0
[0.2.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.2.0
[0.1.0]: https://github.com/projectbin07-stack/printfarm-planner/releases/tag/v0.1.0
