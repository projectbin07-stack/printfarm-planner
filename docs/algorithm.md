# Scheduling algorithm

This document describes what `printfarm-planner` optimises and how. Times are minutes from the
plan start (`--start`). Code references are to `src/printfarm_planner/`.

## 1. Model

- A **job** has `copies` identical **plates**. A plate runs on one printer that can run the job
  (material type, number of filaments against AMS slots, nozzle, bed fit, sliced-for models; see
  `compat.py`). Its print time is the job's time multiplied by the printer's `time_factor`.
- Every plate start is an **operator task** of `plate_change_min`, plus `material_change_min` when
  the printer does not hold every spool the plate needs. The task must fit inside one labour
  window, and each operator does one task at a time. Printing needs nobody.
- A schedule is fully described by the **plate sequence of each printer**. It is turned into
  times by the dispatch rule shared by every planner (`scheduler.Dispatcher`, `simulate`): the
  printer whose next operator slot comes first is served next (ties go to the printer listed
  first in the fleet file), and its next plate is booked at the earliest time an operator is free
  inside a labour window. Nothing is ever started later than that on purpose.

## 2. Objective

For a schedule $S$ (`objective.py`):

$$
J(S) = a_T \sum_j w_j \max(0,\ C_j - d_j) \;+\; a_C\, C_{\max} \;+\; a_{off}\, W_{off} \;+\; a_{in}\, W_{in} \;+\; a_M\, N_M
$$

| Symbol | Term name | Meaning | Unit |
|---|---|---|---|
| $C_j$ | | finish time of the last plate of job $j$ | min |
| $d_j$ | | due time of job $j$; jobs without one contribute 0 | min |
| $w_j$ | | $\max(1,\ 1 + \text{priority}_j)$ | - |
| $\sum_j w_j \max(0, C_j - d_j)$ | `tardiness` | weighted tardiness | min |
| $C_{\max}$ | `makespan` | finish time of the last plate overall | min |
| $W_{off}$ | `wait_off` | time finished plates sit on a printer, before the next plate's setup on that printer starts, **outside** labour windows (overnight / weekend idle) | min |
| $W_{in}$ | `wait_in` | the same waiting **inside** labour windows (operator busy elsewhere, or a window about to close) | min |
| $N_M$ | `material_changes` | plate starts that needed a spool change | count |

$W_{off}$ and $W_{in}$ are exactly the "wait off" and "wait in" figures of `metrics.py`: for each
pair of consecutive plates on a printer, the gap from the first plate's end to the second
plate's setup start, split by the labour calendar. The time after the **last** plate on each
printer is not counted (the plan does not schedule clearing the final plates).

Default weights, all configurable with `--weight NAME=VALUE`:

| Weight | Default | Reasoning |
|---|---|---|
| $a_T$ `tardiness` | 1 | a minute late on a priority-0 job costs as much as a minute of makespan |
| $a_C$ `makespan` | 1 | |
| $a_{off}$ `wait_off` | 0.2 | idle printer time overnight is lost capacity but costs no labour |
| $a_{in}$ `wait_in` | 0.5 | idle time while someone is on shift usually means operator contention |
| $a_M$ `material_changes` | 10 | a spool swap already costs `material_change_min` of operator time; the extra 10 accounts for handling and the risk of loading the wrong spool |

These are starting values chosen by judgement, not fitted to any real farm. Unscheduled jobs
(no capable printer) are the same for every planner and are not part of $J$. Lower is better.
`plan` prints each term, its weight and its contribution in text, JSON (`"objective"`) and HTML.

## 3. Greedy construction (`fill`, `edd`)

Unchanged from v0.1.0 (`scheduler.plan`): repeatedly take the printer whose next operator slot
comes first and give it the best remaining plate it can run, by priority and then by

- `fill`: least time the finished plate would wait for the next labour window, no material
  change, earliest due date, longest print, fewest capable printers;
- `edd`: earliest due date, no material change, longest print, fewest capable printers.

The greedy rules do not look at $J$. Decoding the greedy's own sequences with `simulate`
reproduces it exactly (tested), so both later phases can start from it.

## 4. Local search (`search.py`)

State: the plate sequence of each printer. Each iteration draws one neighbourhood uniformly:

| Neighbourhood | Move |
|---|---|
| `move` | remove a random plate and reinsert it at a different random position on the same printer |
| `swap` | exchange two random plates of different jobs, on the same or different printers, if each printer can run the other plate |
| `reassign` | move a random plate to a different printer that can run it, at a random position |

A draw that does not apply (for example `move` on a printer with one plate) still uses up the
iteration. Each candidate is re-timed with `simulate`, so labour windows, operator capacity,
material changes and compatibility are re-checked by the same code as the greedy planner.

Acceptance, with $\Delta = J(\text{candidate}) - J(\text{current})$:

- **Hill climbing** (`--improve hill`, default in `compare`): accept if $\Delta \le 0$. Accepting
  equal moves lets the search walk across plateaus.
- **Simulated annealing** (`--improve anneal`): accept if $\Delta \le 0$, otherwise with
  probability $e^{-\Delta / T_k}$. Geometric cooling over the iteration budget $N$:

  $$T_k = T_0 \cdot r^{(k-1)/(N-1)}, \qquad k = 1 \ldots N$$

  with $T_0 = \max(1,\ 0.02 \cdot J(\text{start}))$ and $r = 10^{-3}$ (`SearchConfig.t0`,
  `t_end_ratio`), so the temperature falls from $T_0$ to $T_0/1000$ at the last iteration. If a
  time limit stops the run early, the temperature never reached its final value.

The best schedule seen is returned. The start schedule is the first "best", and a candidate
replaces it only when strictly lower, so **the result is never worse than the start** (tested
on every run in the test suite). Random draws come from `random.Random(seed)`. A run that stops
on the iteration budget (`--iterations`, default 2000) is deterministic for a seed. A run stopped
by `--time-limit` depends on machine speed.

On the example order (26 plates) one iteration takes about 0.55 ms on the development machine,
so the default budget runs in a little over a second.

## 5. Exact solver for small instances (`exact.py`)

Depth-first branch-and-bound over the same space of per-printer plate sequences:

- Each node follows the dispatch rule: the open printer with the earliest operator slot either
  receives one of the remaining plates it can run, or is **closed** (gets no more plates). Every
  set of sequences corresponds to exactly one path, so the search is exhaustive.
- Copies of a job are interchangeable, so only the lowest remaining copy number is branched on.
- Children are explored in order of their lower bound and pruned when the bound is not below the
  best complete schedule. The initial upper bound is the best schedule the other methods found.
- Lower bound for a partial schedule: material-change and waiting cost already incurred (they
  never decrease), plus $a_C$ times the larger of the latest end so far and, for each remaining
  plate, its earliest possible finish on any open capable printer (earliest labour slot after
  that printer is free, plus plate change and print time, ignoring operator contention and spool
  changes), plus $a_T$ times the weighted tardiness implied by those per-job finish bounds.

**Limit:** `compare` runs it only for instances with at most 8 schedulable plates
(`--exact-limit`, `EXACT_PLATE_LIMIT`), and a node limit of 2,000,000 stops it with
"best found so far" instead of "optimal". Measured on generated instances with 2-3 printers:
8 plates took up to 0.44 s, 10 plates up to 2.7 s (worst of 10 instances each). Cost grows
roughly factorially with plates, and more printers or distinct jobs make it larger.

**What "optimal" means here:** optimal over plate sequences timed by the dispatch rule. It does
not consider inserting deliberate idle time or letting one operator serve printers in a
different order than the rule, so a hand-made timetable outside this space could in principle
be better. The tests check the solver against brute-force enumeration of every assignment and
order on 16 generated instances of 3-5 plates.

## 6. Measured gaps

`python tests/gap_study.py` runs every method on generated instances (`helpers.random_instance`:
2-3 printers, 1-3 copy jobs of PLA/PETG/ASA, random due dates, priorities, 1 or 2 operators)
with the default weights. Local search used 300 iterations from the better greedy schedule;
gap = $(J - J^*) / J^*$ against the exact optimum $J^*$. Output from the development machine:

```text
40 instances of 5 plates, gap vs exact optimum (exact solver max 0.00 s)
  fill    at best  11/40  mean gap  33.13%  max gap  273.06%
  edd     at best  13/40  mean gap  38.15%  max gap  273.06%
  hill    at best  37/40  mean gap   0.36%  max gap    8.97%  no improvement on 16
  anneal  at best  33/40  mean gap   0.86%  max gap   17.37%  no improvement on 15
40 instances of 7 plates, gap vs exact optimum (exact solver max 0.09 s)
  fill    at best   5/40  mean gap  51.97%  max gap  339.82%
  edd     at best   3/40  mean gap  88.32%  max gap  459.80%
  hill    at best  30/40  mean gap   4.84%  max gap  147.14%  no improvement on 8
  anneal  at best  30/40  mean gap   4.83%  max gap  147.14%  no improvement on 8
20 instances of 20 plates, gap vs best method
  fill    at best   0/20  mean gap  98.49%  max gap  313.03%
  edd     at best   0/20  mean gap 133.95%  max gap  368.82%
  hill    at best  14/20  mean gap   4.93%  max gap   47.57%  no improvement on 0
  anneal  at best   9/20  mean gap   4.72%  max gap   24.18%  no improvement on 0
```

"No improvement" counts instances where the local search returned its start schedule, which
includes cases where the greedy schedule was already optimal. The 20-plate rows (2000
iterations) have no exact reference, so their gap is against the best of the four methods.

How to read this:

- The large greedy gaps come from small instances where $J^*$ is small, so a few hours of
  lateness or waiting is a large percentage.
- On one 7-plate instance (seed 33) both local searches stayed at a local optimum 147% above the
  optimum, and 3000 hill-climbing iterations did not change that: reaching the optimum needs
  several plates to change printer at once, and every single move in between is worse.
- Annealing was not better than hill climbing on these sets at these budgets.
- These instances are generated, not real orders. `tests/test_exact.py` pins the 5- and
  7-plate numbers above so that a change in behaviour is noticed.
