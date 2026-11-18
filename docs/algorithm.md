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
- After its last plate, every printer that ran a plate gets a **clearing task** (remove the final
  plate) of `plate_change_min`. It is an operator task like a plate start: it must fit inside a
  labour window and needs a free operator. (Since v0.3.0; before, final plates were never
  cleared in the plan.) Clearing is assumed to take as long as a plate change, which slightly
  overstates it, because a plate change also starts the next file.
- A schedule is fully described by the **plate sequence of each printer**. It is turned into
  times by the dispatch rule shared by every planner (`scheduler.Dispatcher`, `simulate`): the
  printer whose next operator slot comes first is served next (ties go to the printer listed
  first in the fleet file), and its next plate is booked at the earliest time an operator is free
  inside a labour window. A printer whose sequence is used up takes part in the same rule for
  its clearing task, so plate starts and clearings compete for operators in time order. Nothing
  is ever started later than that on purpose.

## 2. Objective

For a schedule $S$ (`objective.py`):

$$
J(S) = a_T \sum_j w_j \max(0,\ C_j - d_j) \;+\; a_C\, C_{\max} \;+\; a_{off}\, W_{off} \;+\; a_{in}\, W_{in} \;+\; a_M\, N_M
$$

| Symbol | Term name | Meaning | Unit |
|---|---|---|---|
| $C_j$ | | time the last plate of job $j$ finishes printing | min |
| $d_j$ | | due time of job $j$; jobs without one contribute 0 | min |
| $w_j$ | | $\max(1,\ 1 + \text{priority}_j)$ | - |
| $\sum_j w_j \max(0, C_j - d_j)$ | `tardiness` | weighted tardiness | min |
| $C_{\max}$ | `makespan` | end of the last operator task: the last clearing of a final plate | min |
| $W_{off}$ | `wait_off` | time finished plates sit on a printer until the next operator task on that printer starts (the next plate's setup, or for the final plate its clearing), **outside** labour windows (overnight / weekend idle) | min |
| $W_{in}$ | `wait_in` | the same waiting **inside** labour windows (operator busy elsewhere, or a window about to close) | min |
| $N_M$ | `material_changes` | plate starts that needed a spool change | count |

Written out, for printer $p$ with plates $1 \ldots n_p$, plate $k$ ending at $e_{p,k}$, the setup
of plate $k+1$ starting at $s_{p,k+1}$ and the clearing starting at $s_{p,n_p+1}$:

$$
W_{off} = \sum_p \sum_{k=1}^{n_p} \text{off}\big(e_{p,k},\ s_{p,k+1}\big), \qquad
W_{in} = \sum_p \sum_{k=1}^{n_p} \text{in}\big(e_{p,k},\ s_{p,k+1}\big)
$$

where $\text{in}(a, b)$ is the part of $[a, b]$ inside labour windows and
$\text{off}(a, b) = (b - a) - \text{in}(a, b)$, and $C_{\max} = \max_p (s_{p,n_p+1} + c)$ over
printers that ran a plate, with $c$ = `plate_change_min`. These are exactly the "wait off",
"wait in" and makespan figures of `metrics.py`, which also reports the final-plate part of the
wait on its own (`final_wait_min`).

**Changed in v0.3.0.** Up to v0.2.0 the sums ran only to $k = n_p - 1$ (the final plate's wait
was not counted) and $C_{\max}$ was the last print end, with no clearing task in the plan. A
plan whose printers all finished at night therefore showed no waiting at all, although the last
plates sat on the beds until the next staffed window. The local search learned to produce such
plans: on the example order, the v0.2.0 hill-climbing plan was reported as 0 minutes of
off-hours waiting and an objective of 1332.5, but under the v0.3.0 definition the same plate
sequences have 32h 52m of off-hours waiting and an objective of 1907.0.

The tardiness term is unchanged: $C_j$ is when the plate finishes printing, not when it is
taken off the bed. A job whose last plate finishes at 02:00 before a 06:00 due time counts as on
time even though nobody can collect it before 08:00. This is a known simplification.

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

The plate choice is unchanged from v0.1.0 (`scheduler.plan`): repeatedly take the printer whose
next operator slot comes first and give it the best remaining plate it can run, by priority and
then by

- `fill`: least time the finished plate would wait for the next labour window, no material
  change, earliest due date, longest print, fewest capable printers;
- `edd`: earliest due date, no material change, longest print, fewest capable printers.

When the printer that comes first can run none of the remaining plates and has a finished plate
on its bed, it gets its clearing task instead; the remaining clearings are booked in the same
order after the last plate. Since v0.3.0 these clearings take operator time, so a later plate
start can move a few minutes. On the example order the `fill` plan's plate times did not change;
in the `edd` plan, clearing P1S-2 at Tuesday 08:05 delays seven later plate starts by 5 minutes.
The greedy rules do not look at $J$. Decoding the greedy's own sequences with `simulate`
reproduces it exactly, clearings included (tested), so both later phases can start from it.

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

On the example order (26 plates) one iteration takes about 0.7 ms on the development machine,
so the default budget runs in about 1.4 s.

## 5. Exact solver for small instances (`exact.py`)

Depth-first branch-and-bound over the same space of per-printer plate sequences:

- Each node follows the dispatch rule. The printer with the earliest operator slot, among open
  printers that can run a remaining plate and printers with an uncleared final plate, is served:
  if it can take a plate, it either receives one of the remaining plates it can run or is
  **closed** (gets no more plates); otherwise its final plate is cleared. Closing a printer that
  has run a plate books its clearing at that point, which is exactly where `simulate` books it
  when the printer's sequence runs out. When no plates remain, the open clearings are booked in
  dispatch order. Every set of sequences corresponds to exactly one path, so the search is
  exhaustive.
- Copies of a job are interchangeable, so only the lowest remaining copy number is branched on.
- Children are explored in order of their lower bound and pruned when the bound is not below the
  best complete schedule. The initial upper bound is the best schedule the other methods found.
- Lower bound for a partial schedule, with $f(t)$ the earliest start $\ge t$ of a
  `plate_change_min` task inside a labour window:
  - material-change and waiting cost already incurred (they never decrease);
  - for every printer with an uncleared plate that ended at $r$: the weighted wait over
    $[r, f(r)]$, because its next operator task (plate setup or clearing) cannot start earlier;
  - $a_C$ times the largest of: the latest clearing end so far; $f(r) + c$ for each printer with
    an uncleared plate; and, for each remaining plate with earliest possible finish $e$ on any
    open capable printer (earliest labour slot after that printer is free, plus plate change and
    print time, ignoring operator contention and spool changes), $f(e) + c$, because that plate
    must still be cleared or followed by another setup;
  - $a_T$ times the weighted tardiness implied by those per-job finish bounds.

  Every part only ignores delays (operator contention, longer setups), and $f$ is
  non-decreasing, so the bound never exceeds the true objective of any completion.

**Limit:** `compare` runs it only for instances with at most 8 schedulable plates
(`--exact-limit`, `EXACT_PLATE_LIMIT`), and a node limit of 2,000,000 stops it with
"best found so far" instead of "optimal". Measured on generated instances
(`helpers.random_instance`, seeds 0-9, 2-3 printers), seeded with the better greedy plan:
8 plates took up to 0.25 s (1,845 nodes), 10 plates up to 7.0 s (50,249 nodes), worst of 10
instances each. (The v0.2.0 documentation gave 0.44 s and 2.7 s for its own measurement without
clearing; the instances and setup were not recorded exactly, so the two are not directly
comparable.) Cost grows roughly factorially with plates, and more printers or distinct jobs make
it larger.

**What "optimal" means here:** optimal over plate sequences timed by the dispatch rule. It does
not consider inserting deliberate idle time or letting one operator serve printers in a
different order than the rule, so a hand-made timetable outside this space could in principle
be better. The tests check the solver against brute-force enumeration of every assignment and
order on 16 generated instances of 3-5 plates (half with the default weights, half with weights
that make waiting and material changes dominate). The brute force times each candidate with
`simulate`, so since v0.3.0 it includes the clearing tasks, and all 16 instances agree.

## 6. Measured gaps

`python tests/gap_study.py` runs every method on generated instances (`helpers.random_instance`:
2-3 printers, 1-3 copy jobs of PLA/PETG/ASA, random due dates, priorities, 1 or 2 operators)
with the default weights. Local search used 300 iterations from the better greedy schedule;
gap = $(J - J^*) / J^*$ against the exact optimum $J^*$. Output from the development machine:

```text
40 instances of 5 plates, gap vs exact optimum (exact solver max 0.02 s)
  fill    at best   7/40  mean gap  30.51%  max gap  308.61%
  edd     at best   7/40  mean gap  32.91%  max gap  308.61%
  hill    at best  36/40  mean gap   0.54%  max gap    8.77%  no improvement on 10
  anneal  at best  35/40  mean gap   0.57%  max gap    7.46%  no improvement on 9
40 instances of 7 plates, gap vs exact optimum (exact solver max 0.25 s)
  fill    at best   4/40  mean gap  40.85%  max gap  308.61%
  edd     at best   2/40  mean gap  63.86%  max gap  308.61%
  hill    at best  28/40  mean gap   3.28%  max gap   78.14%  no improvement on 6
  anneal  at best  34/40  mean gap   2.22%  max gap   78.14%  no improvement on 5
20 instances of 20 plates, gap vs best method
  fill    at best   0/20  mean gap  90.18%  max gap  309.44%
  edd     at best   0/20  mean gap 123.30%  max gap  404.05%
  hill    at best  10/20  mean gap   4.34%  max gap   33.89%  no improvement on 0
  anneal  at best  14/20  mean gap   0.87%  max gap    6.27%  no improvement on 0
```

"No improvement" counts instances where the local search returned its start schedule, which
includes cases where the greedy schedule was already optimal. The 20-plate rows (2000
iterations) have no exact reference, so their gap is against the best of the four methods.

These numbers are for the v0.3.0 objective (final-plate wait counted, clearing scheduled), so
they are not comparable one to one with the v0.2.0 table, which measured a different objective.
For the record, v0.2.0 measured: 5 plates, hill at best 37/40 (mean gap 0.36%), anneal 33/40;
7 plates, hill and anneal at best 30/40 (mean gap 4.84%, max 147.14%); 20 plates, hill at best
14/20, anneal 9/20.

How to read this:

- The large greedy gaps come from small instances where $J^*$ is small, so a few hours of
  lateness or waiting is a large percentage.
- On one 7-plate instance (seed 33) both local searches stayed at a local optimum 78% above the
  optimum, and 3000 hill-climbing iterations did not change that: the optimum puts all three
  copies of one job on the other printer and moves two copies of another job the opposite way,
  and every single move in between is worse.
- With the corrected objective, annealing did better than hill climbing on the 7- and 20-plate
  sets at these budgets (in v0.2.0 it did not). On the 26-plate example it depended on the seed
  (see the README). Treat this as an observation on these instances, not a general rule.
- These instances are generated, not real orders. `tests/test_exact.py` pins the 5- and
  7-plate numbers above so that a change in behaviour is noticed.
